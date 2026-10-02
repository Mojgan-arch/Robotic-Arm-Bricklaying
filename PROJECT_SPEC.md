# Project: Robotic Arm with Multi-Agent LLMs in the Loop
## Assembly of Parametric Structures with Real-Time Trajectory Replanning and Deviation Correction

Repository name: `masonmind`

You are building a complete, runnable research codebase in Python. Read this whole file before writing code. Implement the milestones in order. After each milestone: make sure the code runs, all tests pass, update the README, and commit with a clear message. Do not skip ahead, and do not leave placeholder functions that silently do nothing.

---

## 1. Concept (what the system does)

A user describes a parametric wall in plain text (for example: "a wavy wall, 8 courses high, with a diamond-shaped opening in the middle and more ventilation toward the top").

**Phase A — Generative design before building.** A team of LLM agents turns the request into a complete, verified design *before* construction starts:
1. Clarifier agent asks the user questions if the request is ambiguous.
2. Architect agent writes a design in a small **design language (DSL)** made of pattern rules (rotation rules, opening rules, regions). This is the generative step: it allows varied, creative arrangements, but **LLMs never output raw brick coordinates**.
3. A deterministic compiler turns the DSL program into exact brick poses.
4. A deterministic **Verifier** checks dimensions, overlap between rotated bricks, and support of every brick. Precise failure messages are fed back to the Architect for repair (generate → verify → repair loop).
5. Sequence Strategist agent chooses an assembly **strategy**; a deterministic sequence planner builds the exact placement order and checks precedence, stability of every intermediate state, and gripper clearance.
6. Structural Engineer agent reviews the design together with a physics drop test.
7. Robot Operator agent reviews reachability and motion-planning feasibility.
8. Coordinator agent resolves disagreements and loops back if needed (max N rounds).
9. The approved design is shown to the user and exported as IFC (the "BIM").

**Phase B — Closed-loop construction.** A simulated robotic arm builds the wall following the sequence:
1. For every pick and place, a classical **motion planner** (RRT-Connect + smoothing + time parameterization) computes a collision-free trajectory, including the brick held in the gripper.
2. During motion, the arm monitors the workspace with a depth camera. Speed is scaled down when an obstacle gets close; the arm **stops** inside the safety distance and **replans** from its current state.
3. If no trajectory can be found within a time limit, the problem is escalated to the Site Supervisor agent.
4. After every course, the wall is scanned and compared to the design (Scan-vs-BIM) to estimate each brick's as-built pose and deviation.
5. If deviations exceed tolerance, the Site Supervisor agent **must** decide what to do.
6. A VLM Critic agent checks that the wall still matches the user's intent.
7. A Memory module stores lessons learned and injects them into future prompts.

**Division of labor (state this clearly in the README):** geometry and motion are solved by deterministic algorithms; LLM agents make design and task-level decisions. LLMs never produce coordinates, joint angles, or trajectories.

Disturbances are injected on purpose (placement noise, dropped bricks, broken bricks, material shortage, user changing their mind, moving and blocking obstacles) so the LLM decisions are actually needed.

---

## 2. Tech stack

- Python 3.10+
- `pybullet` + `pybullet_data` for simulation. Use the Franka Panda URDF that ships with `pybullet_data` (`franka_panda/panda.urdf`).
- `numpy`, `scipy`
- `shapely` for 2D footprint geometry (support ratio, overlap)
- `open3d` for point clouds and ICP
- `pydantic` v2 for all data schemas and LLM output validation
- `pyyaml` for configs
- `ifcopenshell` for IFC export (optional: if import fails, log a warning and skip export)
- `imageio` / `imageio-ffmpeg` for video recording
- `pytest` for tests
- `gradio` for an optional demo UI (last milestone only)
- Motion planning: implement RRT-Connect, shortcut smoothing, and trapezoidal time parameterization yourself in `masonmind/planning/` (small, readable, portfolio-friendly). You may consult the `pybullet_planning` package for reference, but the project must not depend on it.
- No LangChain or LangGraph. Write a small, readable orchestrator in plain Python.

Pin versions in `requirements.txt`. Provide a `pyproject.toml` and make the package installable with `pip install -e .`.

---

## 3. LLM provider layer

Create `masonmind/llm/` with a single interface:

```python
class LLMClient(Protocol):
    def complete(self, system: str, messages: list[dict], images: list[bytes] | None = None,
                 json_schema: dict | None = None) -> str: ...
```

Implementations:
- `OpenAIClient` (key from env var `OPENAI_API_KEY`)
- `AnthropicClient` (key from env var `ANTHROPIC_API_KEY`)
- `OllamaClient` (local, e.g. `qwen2.5-vl` for vision, configurable URL)
- `MockClient`: deterministic, offline, returns valid canned JSON for every agent, including at least one deliberately invalid design so the verify → repair loop is exercised. **All tests and the default demo must run with `MockClient` and no API key.**

Rules:
- Provider and model names come from `configs/llm.yaml`, never hard-coded.
- Never hard-code or log API keys.
- Every agent output is validated against its pydantic schema. On validation failure, retry up to 3 times, sending the validation error back to the model. After that, raise a clear error.
- Cache responses on disk (hash of provider+model+prompt); caching can be disabled in config.
- Log every LLM call (agent name, prompt, response, latency, token usage if available) to `runs/<run_id>/llm_log.jsonl`.

---

## 4. Core data schemas (`masonmind/schemas.py`)

### 4.1 Design DSL (output of the Architect)

```python
class Region(BaseModel):
    course_start: int; course_end: int        # inclusive
    u_start: float = 0.0; u_end: float = 1.0  # fraction along wall length

class RotationRule(BaseModel):
    region: Region
    kind: Literal["wave", "zigzag", "twist_gradient", "alternate", "constant"]
    amplitude_deg: float          # 0..45
    frequency: float = 1.0        # waves along the region length
    phase_shift_per_course: float = 0.0
    combine: Literal["add", "override"] = "add"

class OpeningRule(BaseModel):
    region: Region
    kind: Literal["gradient", "diamond", "stripe", "checker", "random"]
    ratio_start: float = 0.0      # 0..0.5, bottom of region
    ratio_end: float = 0.0        # 0..0.5, top of region
    size: float | None = None     # shape size for diamond/stripe, fraction of region

class DesignProgram(BaseModel):
    n_courses: int                # 3..15
    bricks_per_course: int        # 4..14
    bond: Literal["running", "stack"]
    rotation_rules: list[RotationRule]
    opening_rules: list[OpeningRule]
    seed: int
    rationale: str
```

### 4.2 Compiled design, sequence and verification

```python
class Brick(BaseModel):
    id: str            # e.g. "c03_b07"
    course: int; index: int
    x: float; y: float; z: float   # meters, wall frame
    yaw_deg: float
    is_opening: bool

class Design(BaseModel):
    program: DesignProgram
    bricks: list[Brick]
    version: int

class VerificationIssue(BaseModel):
    check: Literal["dimensions", "overlap", "support", "com", "sequence_precedence",
                   "intermediate_stability", "gripper_clearance", "reachability"]
    brick_ids: list[str]
    message: str                   # precise, human-readable, fed back to the LLM
    value: float | None = None; limit: float | None = None

class VerificationReport(BaseModel):
    passed: bool
    issues: list[VerificationIssue]

class SequenceStrategy(BaseModel):
    strategy: Literal["left_to_right", "right_to_left", "center_out",
                      "ends_in", "high_rotation_first", "low_rotation_first"]
    rationale: str

class AssemblySequence(BaseModel):
    order: list[str]               # brick ids, non-opening only
    strategy: SequenceStrategy
    report: VerificationReport
```

### 4.3 Construction-time schemas

```python
class BrickDeviation(BaseModel):
    brick_id: str
    dx_mm: float; dy_mm: float; dz_mm: float
    dyaw_deg: float
    missing: bool
    within_tolerance: bool

class MotionEvent(BaseModel):
    t: float
    kind: Literal["slowdown", "stop", "replan_ok", "replan_failed", "resume"]
    min_distance_m: float
    detail: str

class SupervisorDecision(BaseModel):
    action: Literal["ACCEPT", "REPOSITION", "REPLACE", "REPLAN_REMAINING",
                    "RESEQUENCE", "WAIT", "ASK_USER"]
    brick_ids: list[str] = []
    new_program_for_remaining: DesignProgram | None = None
    new_strategy: SequenceStrategy | None = None
    wait_seconds: float | None = None
    question_for_user: str | None = None
    reasoning: str
```

Add schemas for every agent's output (ClarifierOutput, ArchitectOutput, StructuralReview, ReachabilityReview, CoordinatorDecision, CriticReview, MemoryRule).

---

## 5. Module specifications

### 5.1 DSL compiler (`masonmind/design/compiler.py`)
- Pure, deterministic `compile_program(program: DesignProgram) -> Design`.
- Brick size: 1:4 scale standard brick, `0.054 × 0.026 × 0.016 m` (configurable).
- Rotation rules are applied in order (`add` sums, `override` replaces) and the final yaw is clamped to the configured limit. Rotation is about each brick's own vertical axis.
- Running bond offsets odd courses by half a brick.
- Opening rules mark bricks as openings using the seed and the shape definition.
- The wall is placed in front of the robot base inside the Panda's comfortable workspace.

### 5.2 Verifier (`masonmind/design/verifier.py`)
Deterministic checks, each producing precise `VerificationIssue`s:
- **Dimensions:** total wall size within configured bounds and inside the robot workspace.
- **Overlap:** no two bricks in the same course intersect, using oriented footprints (`shapely` polygons) with a small tolerance.
- **Support:** for each brick above course 0, the fraction of its footprint area resting on the union of footprints directly below must be ≥ `min_support_ratio` (config, default 0.5).
- **Center of mass:** the brick's center projected down must fall inside the supporting area.
- The verifier must be fast (well under one second for a full wall) and fully unit-tested with hand-made passing and failing cases.

### 5.3 Sequence planner (`masonmind/design/sequencer.py`)
- Build a precedence graph: a brick depends on every brick whose footprint supports it.
- Produce an order as a topological sort that follows the chosen `SequenceStrategy` inside each course.
- Check **gripper clearance**: model the open gripper as boxes; sweep it along the vertical approach to the target pose and check collisions with bricks already placed earlier in the sequence.
- Check **intermediate stability**: at configurable checkpoints (default: after each course and every K bricks), run a short physics drop test of the partial structure.
- If the chosen strategy fails, report issues so the Sequence Strategist can pick another.

### 5.4 IFC export (`masonmind/design/ifc_export.py`)
- Export each non-opening brick as an `IfcBuildingElementProxy` with an extruded box and correct placement. Store the assembly order index as a property. Save to `runs/<run_id>/design_v<version>.ifc`.

### 5.5 Simulation (`masonmind/sim/`)
- `env.py`: PyBullet world, ground plane, table, Panda arm, brick supply area, discard area, GUI or DIRECT mode from config.
- `robot.py`: pick-and-place primitives (approach, descend, grasp, lift, transfer, place, release, retreat) that execute trajectories produced by the planner. Use a **constraint-based grasp** (fixed constraint between gripper and brick on contact); real finger grasping is optional and off by default.
- `obstacles.py`: scripted obstacles configured per scenario:
  - `moving`: a box or capsule (a "worker" or "wheelbarrow") that enters the workspace along a path at a given time, stays for a duration, then leaves.
  - `blocking`: an obstacle that parks on or near the target area until the Supervisor decides what to do (or for a configured time).
- `disturbances.py`, configured from `configs/scenarios/*.yaml`:
  - `placement_noise_mm`, `placement_noise_deg` (Gaussian, applied at release)
  - `p_drop` (brick released early)
  - `p_broken` (shorter brick spawned; flagged only in ground truth, agents must detect it)
  - `material_shortage_at_course`
  - `user_change_at_course` with a text message injected as a user request
- `stability.py`: `drop_test(bricks) -> StabilityReport` for full or partial structures.
- `camera.py`: virtual depth camera(s) using `getCameraImage`. Return RGB, depth, segmentation mask; convert depth to a world-frame point cloud. Configurable depth noise.
- `recorder.py`: MP4 video of every run to `runs/<run_id>/video.mp4`, with an overlay showing the current state (moving / slowed / stopped / replanning) and the minimum distance to obstacles.

### 5.6 Motion planning and execution (`masonmind/planning/`)
- `collision.py`: collision checker built on PyBullet (`getClosestPoints`) covering robot self-collision, the static scene, placed bricks, the **brick attached to the gripper**, and perceived obstacles. Configurable safety margin.
- `rrt_connect.py`: RRT-Connect in the 7-DoF joint space with configurable step size, max iterations, and time limit.
- `smoothing.py`: random shortcut smoothing with collision re-checks.
- `time_param.py`: trapezoidal velocity profile respecting joint velocity and acceleration limits.
- `perception.py`: obstacle detection from the depth camera only. Remove points that belong to known bodies (robot, table, placed bricks, supply area) using the segmentation mask and known scene IDs, cluster the remaining points, and convert clusters to boxes in the collision world. **The planner must not read obstacle positions directly from the simulator.**
- `executor.py`: executes a trajectory at a fixed control rate while monitoring:
  - minimum distance between the robot (and held brick) and perceived obstacles
  - speed scaling zones from config: `distance > d_slow` → full speed; `d_stop < distance ≤ d_slow` → speed scaled linearly; `distance ≤ d_stop` → stop
  - whether the remaining path now collides with a perceived obstacle
  - on stop or predicted collision: replan from the current joint state to the same goal; resume when a new path is found
  - if replanning fails for longer than `replan_timeout_s`, return control to the orchestrator, which escalates to the Site Supervisor
  - log every `MotionEvent` to `runs/<run_id>/motion_log.jsonl`

### 5.7 Scan-vs-BIM (`masonmind/scan/`)
- Use the segmentation mask to split the point cloud per brick body.
- For each brick: ICP between observed points and a sampled point cloud of the brick model at its design pose; recover the as-built pose.
- Output `BrickDeviation`s. Tolerances from config (default 3 mm, 2°).
- Also compute deviations from simulation ground truth and log the difference separately (scan accuracy).
- Produce a deviation heatmap image for the README and the VLM Critic.

### 5.8 Agents (`masonmind/agents/`)
Each agent is a class with a system prompt in `masonmind/agents/prompts/<agent>.md`, an input builder, and a pydantic output schema.

| Agent | Input | Output |
|---|---|---|
| Clarifier | user text | clarifying questions or a clean design brief |
| Architect | brief, DSL reference, memory rules, verifier issues, reviews | `DesignProgram` |
| SequenceStrategist | design summary, sequencer issues, memory rules | `SequenceStrategy` |
| StructuralEngineer | program, verifier report, drop-test report, memory rules | approve / reject + requested changes |
| RobotOperator | program, IK reachability and planning-feasibility report | approve / reject + requested changes |
| Coordinator | all reviews | approve, or send back with merged feedback |
| SiteSupervisor | course, deviations, motion events, obstacle status, supply count, remaining design, memory rules | `SupervisorDecision` |
| VLMCritic | rendered RGB image, deviation heatmap, user brief | intent-match score 0..10 + issues |
| MemoryKeeper | an incident and its outcome | zero or more `MemoryRule`s |

The Architect prompt must include a complete reference of the DSL with examples. Prompts explain the role, available actions, the exact JSON format, and the physical meaning of every field.

### 5.9 Memory (`masonmind/memory/`)
- Rules stored as JSON lines in `memory/rules.jsonl`: rule text, evidence (run id, course, brick ids), confidence, times confirmed.
- Retrieve relevant rules (keyword plus recency scoring is enough) and inject them into Architect, SequenceStrategist, StructuralEngineer and SiteSupervisor prompts.
- Memory can be disabled in config for ablation.

### 5.10 Orchestrator (`masonmind/orchestrator.py`)
- `run_phase_a(user_text) -> (Design, AssemblySequence)`: clarify → generate → compile → verify → repair (max rounds) → sequence → reviews → coordinator → IFC export.
- `run_phase_b(design, sequence) -> BuildReport`: for each brick in order: plan → execute with monitoring → place; after each course: scan → compare → supervisor if needed → act → critic → memory.
- Handle every supervisor action concretely:
  - `ACCEPT`: continue.
  - `REPOSITION`: pick the brick and place it again at the design pose.
  - `REPLACE`: move the brick to the discard area and place a fresh one.
  - `REPLAN_REMAINING`: recompile courses not yet built from the new program, keep built courses fixed, re-run verifier, sequencer and drop test before continuing.
  - `RESEQUENCE`: re-plan the order of remaining bricks with a new strategy (e.g. build the other side while an obstacle blocks this one).
  - `WAIT`: pause for `wait_seconds`, re-perceive, retry.
  - `ASK_USER`: interactive prompt in terminal or UI; scripted answer in batch mode.
- Every run writes to `runs/<run_id>/`: config snapshot, programs, designs, sequences, verifier reports, IFC files, scans and deviations, motion log, agent logs, decisions, video, and a final `report.json`.

---

## 6. Experiments (`experiments/`)

System variants (ablation):
- **B0** open loop: fixed design, fixed order, straight-line IK moves, no perception, no LLM during construction.
- **B1** single LLM: one agent does design and supervision; planner without replanning.
- **B2** multi-agent with verifier, sequencer and replanning, no memory.
- **B3** B2 plus memory.

Scenarios in `configs/scenarios/`: `clean`, `noisy_placement`, `drops`, `broken_bricks`, `material_shortage`, `user_change`, `moving_obstacle`, `blocking_obstacle`, `everything`.

Metrics written to `experiments/results.csv`:
- wall completed without collapse (bool), completion time
- mean and max final deviation (mm, deg)
- intent match score (VLM Critic, 0..10)
- generative design: verifier pass rate on first attempt, number of repair rounds, design diversity across seeds
- sequencing: strategy changes, intermediate stability failures
- motion: number of slowdowns, stops, replans, replan success rate, mean replan time, minimum distance to obstacles, collisions (must be zero), path length
- supervisor: interventions per action type
- LLM: number of calls, tokens, estimated cost
- scan accuracy (scan-estimated vs ground-truth deviation)

`experiments/run_all.py` runs every variant × scenario × K seeds. `experiments/plots.py` makes the comparison figures.

---

## 7. Repository layout

```
masonmind/
├── README.md
├── AGENTS.md
├── pyproject.toml
├── requirements.txt
├── configs/
│   ├── default.yaml
│   ├── llm.yaml
│   └── scenarios/
├── masonmind/
│   ├── schemas.py
│   ├── orchestrator.py
│   ├── llm/
│   ├── agents/
│   │   └── prompts/
│   ├── design/        # compiler, verifier, sequencer, ifc_export
│   ├── planning/      # collision, rrt_connect, smoothing, time_param, perception, executor
│   ├── sim/
│   ├── scan/
│   └── memory/
├── experiments/
├── scripts/
│   ├── demo.py
│   └── app.py
├── tests/
└── runs/              # git-ignored
```

---

## 8. Milestones (implement in this order)

**M0 — Skeleton.** Package layout, configs, schemas, logging, `MockClient`, pytest setup. Acceptance: `pytest` passes; `python -m masonmind --help` works.

**M1 — Simulation and basic pick-and-place.** Env, Panda, supply area, constraint grasp, straight-line IK moves, video recording. Acceptance: `scripts/demo.py --milestone 1` places 5 bricks in a row and saves a video.

**M2 — DSL compiler and Verifier.** Acceptance: unit tests with hand-made programs that pass and that fail each check (overlap, support, center of mass, dimensions) with correct messages; compiler is deterministic; at least 3 visually different walls compile and pass.

**M3 — Sequence planner and open-loop baseline B0.** Precedence graph, strategies, gripper clearance, intermediate stability. Acceptance: complete walls built in simulation with two different strategies; a test case where one strategy fails on gripper clearance and another succeeds.

**M4 — Motion planning with static obstacles.** Collision checker (including the held brick), RRT-Connect, smoothing, time parameterization; replace straight-line moves. Acceptance: with a static obstacle placed between supply and wall, every brick is placed with zero collisions; report planning times.

**M5 — Phase A with agents.** Clarifier, Architect (DSL), verify → repair loop, SequenceStrategist, StructuralEngineer, RobotOperator, Coordinator, IFC export. Acceptance: with `MockClient`, text in → verified design, sequence and IFC out, with at least one repair round exercised; the same command works with a real provider when a key is set.

**M6 — Perception: obstacles and Scan-vs-BIM.** Depth-camera obstacle detection and per-brick ICP deviations, heatmap, scan-accuracy logging. Acceptance: perceived obstacle boxes enclose the true obstacle with a reported error; with placement noise, estimated deviations are within 1 mm / 1° of ground truth on average (report actual numbers).

**M7 — Real-time replanning and closed-loop Phase B.** Monitored executor with speed scaling, stop, replan, escalation; SiteSupervisor with all seven actions; all disturbances. Acceptance: in `moving_obstacle` the arm slows, stops, replans and finishes with zero collisions; in `blocking_obstacle` the Supervisor chooses `WAIT` or `RESEQUENCE` and the wall is completed; in `noisy_placement` and `broken_bricks` the final deviation is lower than B0 (show numbers).

**M8 — VLM Critic and Memory.** Acceptance: critic scores in the report; memory rules written after incidents and visible in later prompts (show an example in the README).

**M9 — Experiments.** All variants × scenarios × seeds, results CSV and plots. Acceptance: `experiments/run_all.py --quick` finishes with `MockClient` and produces plots.

**M10 — Polish.** README with a GIF or video at the top, architecture diagram, installation, quick start, DSL reference, configuration guide, results table, limitations. Optional Gradio app: text box → clarifying questions → design preview with verifier report → sequence animation → live build video with motion state → per-course deviation table → agent conversation log.

---

## 9. Engineering rules

- Type hints everywhere; docstrings on public functions.
- No magic numbers in code: physical constants, tolerances, distances and thresholds live in YAML configs.
- Seed everything (numpy, Python `random`, planners, disturbances) so runs are reproducible.
- Keep simulation, perception, planning and LLM code decoupled: the orchestrator is the only place they meet.
- Fail loudly with clear messages; never swallow exceptions silently.
- Tests must not require a GPU, a network connection, or an API key.
- Keep functions small and readable; this repo is also a portfolio piece.

## 10. Do not

- Do not let any LLM output raw brick coordinates, joint angles, or trajectories.
- Do not hard-code API keys or commit `runs/` outputs.
- Do not use simulator ground truth inside the loop: agents, the planner and the executor only use what perception reports. Ground truth is for evaluation only.
- Do not add heavy frameworks beyond the stack above without a clear reason written in the README.
