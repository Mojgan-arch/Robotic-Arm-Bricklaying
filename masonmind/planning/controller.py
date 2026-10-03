"""Static-scene planning and time-parameterized motor execution."""
import time
from pathlib import Path
import numpy as np
from masonmind.io import append_json
from masonmind.planning.collision import CollisionChecker
from masonmind.planning.rrt_connect import rrt_connect, PlanningError
from masonmind.planning.smoothing import shortcut
from masonmind.planning.time_param import parameterize


class StaticController:
    """Connect classical planning to the Panda's pick/place primitives."""

    def __init__(self, checker: CollisionChecker, log_path: Path | None = None) -> None:
        self.checker, self.arm = checker, checker.arm
        self.config = checker.world.config["planning"]
        self.log_path = log_path
        self.durations: list[float] = []
        self.path_length = 0.0

    def plan(self, start: np.ndarray, goal: np.ndarray) -> list[np.ndarray]:
        """Plan and smooth, recording actual compute time."""
        started = time.perf_counter()
        try:
            path = rrt_connect(start, goal, self.arm.lower, self.arm.upper, self.checker,
                               self.config, self.arm.world.config["seed"])
        except PlanningError as error:
            raise PlanningError(f"{self.arm.phase}: {error}; last check: {self.checker.last_collision}") from error
        path = shortcut(path, self.checker, self.config["edge_resolution"],
                        self.config["smoothing_iterations"], self.arm.world.config["seed"])
        elapsed = time.perf_counter() - started
        self.durations.append(elapsed)
        self.path_length += sum(float(np.linalg.norm(b - a)) for a, b in zip(path, path[1:]))
        if self.log_path:
            append_json(self.log_path, {"kind": "plan", "duration_s": elapsed, "waypoints": len(path), "phase": self.arm.phase, "simulation_t": self.arm.world.t})
        return path

    def execute(self, path: list[np.ndarray]) -> None:
        """Drive motors along a bounded trajectory at the physics control rate."""
        dt = self.arm.world.sim["dt"]
        trajectory = parameterize(path, self.config["velocity"], self.config["acceleration"], dt)
        elapsed = 0.0
        while elapsed < trajectory.t[-1]:
            elapsed = min(trajectory.t[-1], elapsed + dt)
            command = np.array([np.interp(elapsed, trajectory.t, trajectory.q[:, j]) for j in range(7)])
            self.arm.command(command)
            self.arm.world.step()
        self.arm.world.step(self.arm.world.sim["settle_steps"])
