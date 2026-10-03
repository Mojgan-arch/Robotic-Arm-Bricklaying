"""Scripted disturbances. Only cameras expose them to the controller."""
import numpy as np
import pybullet as p
from masonmind.sim.env import World


class ScriptedObstacle:
    """Enter, dwell and leave along a configured path in simulation time."""

    def __init__(self, world: World, kind: str):
        self.world, self.kind = world, kind
        self.cfg = world.config["construction"]
        self.body = world.box(self.cfg["obstacle_size"], self.cfg["obstacle_start"], color=(.15, .55, .85, 1))
        self.started = world.t
        world.on_step.append(self.tick)

    def tick(self) -> None:
        """Move the disturbance; planners must not read this state."""
        elapsed = self.world.t - self.started - self.cfg["obstacle_start_s"]
        travel = self.cfg["obstacle_travel_s"]
        dwell = self.cfg["blocking_dwell_s"] if self.kind == "blocking" else self.cfg["obstacle_dwell_s"]
        fraction = 0 if elapsed < 0 else min(1, elapsed / travel)
        if elapsed > travel + dwell:
            fraction = max(0, 1 - (elapsed - travel - dwell) / travel)
        position = np.asarray(self.cfg["obstacle_start"]) + fraction * (np.asarray(self.cfg["obstacle_end"]) - self.cfg["obstacle_start"])
        p.resetBasePositionAndOrientation(self.body, position, [0, 0, 0, 1], physicsClientId=self.world.cid)
