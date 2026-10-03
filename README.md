# MasonMind

Research implementation of robotic masonry with a Franka Panda and task-level LLM agents.

**Division of labor:** deterministic algorithms solve geometry and motion. LLM agents
make design and task-level decisions. LLMs never produce coordinates, joint angles,
or trajectories. The default provider is a deterministic offline mock.

## Installation

Python 3.10+ (local environment uses Python 3.11). From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m masonmind --help
pytest
```

Configuration lives in `configs/default.yaml` and `configs/llm.yaml`.
Generated artifacts belong in ignored `runs/`; credentials belong in environment variables.

Windows installs the `pybullet-arm64==3.2.8` distribution, which also ships x86-64
wheels and imports as `pybullet`. This is a packaging fork, not a different physics
engine. Upstream `pybullet==3.2.7` has no Windows wheels and requires MSVC to build.
See [upstream files](https://pypi.org/project/pybullet/3.2.7/) and
[Windows wheel distribution](https://pypi.org/project/pybullet-arm64/).

## Milestone status

- M0: package, strict schemas, all nine offline agent outputs, configuration,
  artifact logging and CLI. Acceptance: 11 tests passed; CLI help works.
- M1: isolated PyBullet world, Panda, supply, finger-contact-checked fixed grasp,
  motor-driven joint interpolation and RGB-D camera with MP4 recorder.
  Acceptance: five bricks placed; physics pick/place test passes (12 tests total).
- M2: seeded DSL compiler and footprint verifier. Acceptance: 24 tests pass,
  including three distinct 20-degree patterns, hand-made failures for every check,
  running bond, rule composition and a full-size verifier runtime below one second.
- M3: precedence graph, six strategies, vertical finger-box clearance, physics
  checkpoints and open-loop B0 construction. Acceptance: 27 tests pass; both
  left-to-right and right-to-left build 12-brick walls. Measured maximum final
  errors: 3.67 mm and 1.46 mm respectively. A clearance test passes only one order.
- M4: seven-joint RRT-Connect, shortcut smoothing, trapezoidal joint profiles,
  robot/scene/self/held-brick collision checking. Acceptance: 32 tests pass;
  both 12-brick builds report zero contact ticks with the fixed obstacle.
  First acceptance run: mean planning time 3.8 ms, worst query 264 ms; maximum
  placement errors 6.66 / 4.33 mm (not yet corrected by perception).
- M5: bounded multi-agent Phase A, audited providers, output validation/retries,
  content-addressed cache, IFC4 export. Acceptance: 35 offline tests pass; mock
  repairs one deliberately unsupported program, approves 12 bricks, and exports
  an IFC with one geometric element and order property per brick.
- M6: two-view depth unprojection, masked unknown-object clustering, per-brick
  point-to-plane Open3D ICP, heatmap and separate ground-truth evaluation.
  Acceptance: 37 tests pass; noisy six-brick course mean scan error **0.027 mm /
  0.014 degrees** with ideal depth. The padded perceived obstacle box encloses
  the known test obstacle. These are synthetic-sensor results, not real-camera claims.
- M7: monitored execution, speed zones, stop/replan/escalation, scripted obstacles,
  release noise/drop/damaged stock, stock and brief events, all seven supervisor
  actions. 44 offline tests pass, including physical reposition/replacement.
  Full moving-obstacle run: 12 bricks, 1 stop, 1 successful replan, zero obstacle
  contact ticks. Blocking run: supervisor WAIT after timed-out replanning,
  completion with zero obstacle contact ticks. Noise comparison is recorded below.
- M8: VLM Critic with image inputs and explicit mock-score labelling; append-only
  memory with evidence, confirmation counts, keyword/confidence/recency retrieval.
  Acceptance: 46 tests pass; noisy-placement incidents write rules, and a subsequent
  Architect call includes a previously stored rule in its audited prompt.
- M9–M10: pending implementation and acceptance runs.

```powershell
python scripts/demo.py --milestone 1
```

The command saves `video.mp4`, `final.png`, a configuration snapshot and a measured
placement report in a unique `runs/` directory. Use `--no-video` for faster tests.
The M1 controller interpolates joint targets and is an open-loop baseline; it has
no obstacle avoidance yet. It never teleports bricks to make placement succeed.

`python scripts/demo.py --milestone 2` saves three verified designs and PNG previews.
Brick centres use metres in the simulation world frame. The wall runs along X and
is centred at `geometry.origin`; its Z coordinate denotes the table surface.
Rotation is around each brick's own Z axis. Phase shifts are radians per course.
Regions include their course bounds; `u` uses the cell centre along the wall.
Diamond uses a normalized Manhattan-distance shape; stripe uses a centred interval.
Gradient/random openings use seeded Bernoulli sampling; checker further restricts
the alternating parity cells. Geometry may reject artistic requests when they
create unsupported bricks; the compiler does not silently fill openings.

`python scripts/demo.py --milestone 3 --no-video` runs both B0 orders. Final descent
and retreat use sampled Cartesian vertical lines; joint interpolation alone can
sweep a carried brick sideways into an adjacent brick. Drop tests measure motion
over a finite settling period, not a proof of structural safety under future loads.

`python scripts/demo.py --milestone 4 --no-video` adds a fixed obstacle and reports
planning times and obstacle contact ticks. Default pitch is now 62 mm, leaving
clearance around 54 mm bricks. Collision paths are discretely sampled, not
continuous-time certified. Intended finger-grasp and upward supporting contacts
are permitted only during relevant approach/lift/place phases with a penetration
limit; other known objects remain collision geometry. Motor tracking and physics
still produce placement error, which must be measured rather than assumed zero.

## Agents and providers

`python scripts/demo.py --milestone 5 --text "A three-course wall"` runs Phase A.
MockClient is an integration fixture, not a language interpreter: it returns a
small stable wall and deliberately triggers one geometric repair. Real text-to-DSL
generation requires changing `provider` and `model` in `configs/llm.yaml`. Use
`openai` with `OPENAI_API_KEY`, `anthropic` with `ANTHROPIC_API_KEY`, or `ollama`
with a local server URL. No paid API calls were made during development.
Live-provider transports are implemented but require a configured compatible
model and have not been acceptance-tested against paid endpoints.

Provider references: [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create),
[Ollama chat](https://docs.ollama.com/api/chat). Every response is validated locally;
an invalid response gets three repair retries. Cache keys include model, provider,
endpoint, prompt, schema and image hashes. `llm_log.jsonl` includes all attempts,
responses, validation errors, latency and available token usage, never credentials.

Phase A robot review currently checks target IK and empty-scene approach planning;
carried-object paths through the evolving scene are checked at construction time.
An agent cannot override a failed deterministic check.

## Perception

Install `pip install -e ".[scan,ifc]"` and run `python scripts/demo.py --milestone 6`.
The control-side detector receives only image-derived points and known-body IDs;
it has no simulator pose access. Brick IDs use simulated instance segmentation,
which is an optimistic association assumption. ICP registers visible observations
to a sampled nominal box; evaluation reads ground truth in a separate module.
Missing means **unobserved**, which can also mean occluded. Obstacle bounds cover
observed surfaces with configurable padding, not all possible hidden geometry.
TinyRenderer's integer-X / inverted-Y pixel convention is tested by calibrated
boxes; using a generic pixel-centre offset introduces millimetre-scale bias.

## Closed-loop construction

```powershell
python scripts/demo.py --milestone 7 --scenario moving_obstacle --no-video
python scripts/demo.py --milestone 7 --scenario blocking_obstacle --no-video
python scripts/demo.py --milestone 7 --scenario noisy_placement --variant B0 --no-video
```

Control uses invisible collision proxies made from design/scan poses and perceived
obstacle bounds. Ground-truth poses are read only by simulation mechanics and
separate evaluation. Every course scan includes the entire built structure.
Loaded bricks cannot be extracted until their dependents are addressed; such an
unsafe requested correction fails explicitly. New programs preserve built courses
and must pass geometry, sequence and drop checks before continuing.
Batch user responses are explicit configuration entries and are recorded. Supply
shortage uses `replenish`; a changed brief uses `keep_current_design` by default.
Pass an `ask_user` callback to Orchestrator for real interaction or revised DSL.

The low-scale contact model uses configurable damping, friction anchors and small
collision margins to prevent numerical drift of 54 mm bricks. During vertical
extraction, fitted brick proxies allow 0.2 mm contact uncertainty; this exception
never applies to unknown obstacles. An approaching obstacle may continue closer
after the arm stops: observed minimum clearance was about 11 mm in the scripted
test, despite the 50 mm stop trigger. This is not a physical safety guarantee.

Broken-stock comparison (seed 1): the B0 mean pose error is 0.025 mm, but its
mean geometry deviation including size mismatch is 2.046 mm. Replacement removes
the damaged stock; the closed-loop pose error was 0.027 mm. There is **no measured
improvement in pose-centre error** for this particular broken-stock seed; shape
conformance is the relevant improvement. Keep these metrics distinct.

Noisy-placement comparison (seed 7): B0 mean/max final displacement **7.332 /
17.696 mm**, with a course-height loss detected; closed loop **0.444 / 2.710 mm**,
completed after three REPOSITION decisions, zero obstacle contacts.

## Memory and critique

Memory is enabled only in B3 construction (and configurable in standalone Phase A).
Example rule written after an actual correction:

> Inspect each completed course and correct measured placement errors before adding load.

Its record includes `run_id`, `course`, `brick_ids`, `confidence: 0.6`, and an
increasing `times_confirmed`. Later Architect, Strategist, StructuralEngineer and
Supervisor inputs contain a `memory_rules` list; inspect `llm_log.jsonl` to audit it.
Set `memory.enabled: false` to disable both reading and writing.
The mock critic always returns 7/10 with an explicit fixture warning. This checks
the integration and cannot support a scientific claim about design intent.

This is simulation research software. It is not a certified controller for hardware.
