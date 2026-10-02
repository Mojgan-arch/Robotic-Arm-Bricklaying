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
- M3–M10: pending implementation and acceptance runs.

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

This is simulation research software. It is not a certified controller for hardware.
