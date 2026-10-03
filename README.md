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
- M6–M10: pending implementation and acceptance runs.

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

This is simulation research software. It is not a certified controller for hardware.
