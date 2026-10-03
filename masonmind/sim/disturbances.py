"""Seeded physical disturbances; hidden flags remain simulation-only."""
import numpy as np
import pybullet as p
from masonmind.sim.env import World


class Disturbances:
    """Inject controlled errors independently from planning and agent evidence."""

    def __init__(self, world: World, scenario: dict):
        self.world, self.scenario = world, scenario
        self.rng = np.random.default_rng(world.config["seed"])

    def stock_length_scale(self) -> float:
        """Choose stock damage without exposing the damage flag to agents."""
        return self.world.config["construction"]["broken_length_scale"] if self.rng.random() < self.scenario["p_broken"] else 1.0

    def should_drop(self) -> bool:
        """Choose an early release for this original placement."""
        return bool(self.rng.random() < self.scenario["p_drop"])

    def on_release(self, body: int) -> None:
        """Perturb the released body; this is injection, never a pose observation."""
        position, quaternion = p.getBasePositionAndOrientation(body, physicsClientId=self.world.cid)
        displacement = self.rng.normal(0, self.scenario["placement_noise_mm"] / 1000, 2)
        euler = list(p.getEulerFromQuaternion(quaternion))
        euler[2] += np.deg2rad(self.rng.normal(0, self.scenario["placement_noise_deg"]))
        position = [position[0] + displacement[0], position[1] + displacement[1], position[2]]
        p.resetBasePositionAndOrientation(body, position, p.getQuaternionFromEuler(euler), physicsClientId=self.world.cid)
