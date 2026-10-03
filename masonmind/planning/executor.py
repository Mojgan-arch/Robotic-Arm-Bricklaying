"""Monitored speed scaling, stop, bounded replan and task-level escalation."""
from collections.abc import Callable
from pathlib import Path
import time
import numpy as np
import pybullet as p
from masonmind.io import append_json
from masonmind.schemas import MotionEvent
from masonmind.planning.controller import StaticController
from masonmind.planning.collision import CollisionChecker
from masonmind.sim.recorder import Recorder
from masonmind.planning.rrt_connect import PlanningError, edge_free
from masonmind.planning.time_param import parameterize


def speed_scale(distance: float, stop: float, slow: float) -> float:
    """Continuous linear speed zone, with a hard zero at the stop boundary."""
    if not 0 <= stop < slow:
        raise ValueError("Require 0 <= d_stop < d_slow")
    return float(np.clip((distance - stop) / (slow - stop), 0, 1))


class MonitoredExecutor(StaticController):
    """Perception-driven execution; unknown obstacle ground truth is inaccessible."""

    def __init__(self, checker: CollisionChecker, perceive: Callable[[], list[int]], log_path: Path, recorder: Recorder | None = None) -> None:
        super().__init__(checker, log_path.parent / "planning.jsonl")
        self.perceive, self.motion_log, self.recorder = perceive, log_path, recorder
        self.events: list[MotionEvent] = []
        self.obstacle_ids: list[int] = []
        self.next_observation = 0.0
        self.minimum_distance = float("inf")
        self.allow_replan = True
        self.replan_times: list[float] = []

    def observe(self, force: bool = False) -> None:
        """Refresh the measurement-driven proxy scene on the sensing schedule."""
        if force or self.arm.world.t >= self.next_observation:
            self.obstacle_ids = self.perceive()
            self.next_observation = self.arm.world.t + self.arm.world.config["scan"]["perception_interval_s"]

    def distance(self) -> float:
        """Measure separation from the current robot/held brick to sensed proxies."""
        distance = float("inf")
        for obstacle in self.obstacle_ids:
            for body in [self.arm.body] + ([] if self.arm.held is None else [self.arm.held]):
                contacts = p.getClosestPoints(body, obstacle, self.config["d_slow"] * 2, physicsClientId=self.arm.world.cid)
                distance = min(distance, min((c[8] for c in contacts), default=float("inf")))
        self.minimum_distance = min(self.minimum_distance, distance)
        return distance

    def event(self, kind: str, distance: float, detail: str) -> None:
        """Persist a monitoring transition, using a finite search horizon if clear."""
        value = distance if np.isfinite(distance) else self.config["d_slow"] * 2
        event = MotionEvent(t=self.arm.world.t, kind=kind, min_distance_m=value, detail=detail)
        self.events.append(event)
        append_json(self.motion_log, event)
        if self.recorder:
            self.recorder.state, self.recorder.distance = kind, value

    def replan(self, goal: np.ndarray) -> list[np.ndarray]:
        """Hold current encoders while retrying from newly perceived geometry."""
        if not self.allow_replan:
            raise PlanningError("Replanning disabled by ablation variant")
        world = self.arm.world
        deadline = world.t + self.config["replan_timeout_s"]
        started = time.perf_counter()
        while world.t < deadline:
            self.arm.command(self.arm.state())
            self.observe(force=True)
            if self.distance() > self.config["d_stop"]:
                try:
                    path = self.plan(self.arm.state(), goal)
                except PlanningError:
                    path = None
                if path:
                    self.replan_times.append(time.perf_counter() - started)
                    self.event("replan_ok", self.distance(), "New path from measured current encoders")
                    self.event("resume", self.distance(), "Resume toward original goal")
                    return path
            world.step(max(1, int(world.config["scan"]["perception_interval_s"] / world.sim["dt"])))
        self.event("replan_failed", self.distance(), "Replanning budget expired; SiteSupervisor required")
        raise PlanningError("Replanning timed out; escalate to SiteSupervisor")

    def execute(self, path: list[np.ndarray]) -> None:
        """Advance simulated control ticks, sensing and revalidating remaining edges."""
        world, cfg = self.arm.world, self.config
        trajectory = parameterize(path, cfg["velocity"], cfg["acceleration"], cfg["control_dt"])
        progress, slowed = 0.0, False
        while progress < trajectory.t[-1]:
            observation_due = world.t >= self.next_observation
            self.observe()
            distance = self.distance()
            scale = speed_scale(distance, cfg["d_stop"], cfg["d_slow"])
            if scale < 1 and not slowed:
                self.event("slowdown", distance, "Obstacle entered linear speed-scaling zone")
                slowed = True
            if scale == 1:
                slowed = False
            index = min(len(trajectory.q) - 1, int(np.searchsorted(trajectory.t, progress)))
            # Revalidate the entire remaining path when measurements change.
            sampled = list(trajectory.q[index::8])
            if len(sampled) == 0 or not np.array_equal(sampled[-1], trajectory.q[-1]):
                sampled.append(trajectory.q[-1])
            blocked = observation_due and self.obstacle_ids and any(
                not edge_free(a, b, self.checker, cfg["edge_resolution"])
                for a, b in zip(sampled, sampled[1:]))
            if scale <= 0 or blocked:
                self.event("stop", distance, "Safety boundary or predicted path collision")
                self.arm.command(self.arm.state())
                path = self.replan(trajectory.q[-1])
                trajectory = parameterize(path, cfg["velocity"], cfg["acceleration"], cfg["control_dt"])
                progress = 0.0
                continue
            progress = min(trajectory.t[-1], progress + cfg["control_dt"] * scale)
            command = np.array([np.interp(progress, trajectory.t, trajectory.q[:, j]) for j in range(7)])
            self.arm.command(command)
            world.step(max(1, round(cfg["control_dt"] / world.sim["dt"])))
        world.step(world.sim["settle_steps"])
