"""Collision proxies constructed from explicit design or sensor measurements."""
import numpy as np
import pybullet as p
from masonmind.planning.perception import ObstacleBox
from masonmind.sim.env import World


class ProxyScene:
    """Invisible query-only geometry; it cannot push objects in the physics world."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.bricks: dict[str, int] = {}
        self.obstacles: list[int] = []

    def box(self, size: list[float], center: list[float], yaw: float = 0) -> int:
        """Create a proxy with physical collision response disabled."""
        shape = p.createCollisionShape(p.GEOM_BOX, halfExtents=(np.asarray(size) / 2).tolist(), physicsClientId=self.world.cid)
        body = p.createMultiBody(0, shape, -1, center, p.getQuaternionFromEuler([0, 0, np.deg2rad(yaw)]), physicsClientId=self.world.cid)
        p.setCollisionFilterGroupMask(body, -1, 0, 0, physicsClientId=self.world.cid)
        p.changeVisualShape(body, -1, rgbaColor=[0, 0, 0, 0], physicsClientId=self.world.cid)
        self.world.known_ids.add(body)
        return body

    def update_brick(self, name: str, position: list[float], yaw: float, size: list[float] | None = None) -> None:
        """Install the nominal or measured pose supplied by the orchestrator."""
        self.remove_brick(name)
        self.bricks[name] = self.box(size or self.world.config["geometry"]["brick_size"], position, yaw)

    def remove_brick(self, name: str) -> None:
        """Remove a known proxy before picking its associated brick."""
        if name in self.bricks:
            body = self.bricks.pop(name)
            p.removeBody(body, physicsClientId=self.world.cid)
            self.world.known_ids.discard(body)

    def update_obstacles(self, boxes: list[ObstacleBox]) -> None:
        """Replace perceived geometry, never consult hidden simulator obstacles."""
        for body in self.obstacles:
            p.removeBody(body, physicsClientId=self.world.cid)
            self.world.known_ids.discard(body)
        self.obstacles = [self.box(box.size.tolist(), box.center.tolist()) for box in boxes]
