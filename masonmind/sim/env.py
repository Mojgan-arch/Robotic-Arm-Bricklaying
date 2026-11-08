"""Isolated PyBullet simulation; all operations specify their client ID."""
from collections.abc import Callable
from typing import Any
import numpy as np
import pybullet as p
import pybullet_data
from masonmind.config import load_config


class World:
    """Own the physics client and deterministic scene objects."""

    def __init__(self, config: dict[str, Any] | None = None, robot: bool = True) -> None:
        self.config = config or load_config()
        self.sim = self.config["sim"]
        self.cid = p.connect(p.GUI if self.sim["gui"] else p.DIRECT)
        if self.cid < 0:
            raise RuntimeError("Could not create a PyBullet client")
        p.setAdditionalSearchPath(pybullet_data.getDataPath(), physicsClientId=self.cid)
        p.setGravity(0, 0, self.sim["gravity"], physicsClientId=self.cid)
        p.setTimeStep(self.sim["dt"], physicsClientId=self.cid)
        p.setPhysicsEngineParameter(deterministicOverlappingPairs=1,
                                   numSolverIterations=self.sim.get("solver_iterations", 100),
                                   numSubSteps=self.sim.get("substeps", 0), physicsClientId=self.cid)
        self.plane = p.loadURDF("plane.urdf", physicsClientId=self.cid)
        p.changeVisualShape(self.plane, -1, textureUniqueId=-1, rgbaColor=[1, 1, 1, 1], physicsClientId=self.cid)
        self.table = self.box(self.sim["table_size"], self.sim["table_center"], mass=0, color=(0.28, 0.34, 0.40, 1))
        self.robot = p.loadURDF("franka_panda/panda.urdf", useFixedBase=True,
                               flags=p.URDF_USE_SELF_COLLISION, physicsClientId=self.cid) if robot else None
        from masonmind.sim.studio import apply_studio
        apply_studio(self.cid, self.plane, self.robot)
        self.known_ids = {self.plane, self.table}
        if self.robot is not None:
            self.known_ids.add(self.robot)
        self.bricks: dict[str, int] = {}
        self.t = 0.0
        self.on_step: list[Callable[[], None]] = []

    def box(self, size: list[float], position: list[float], mass: float = 0,
            yaw_deg: float = 0, color: tuple = (0.72, 0.30, 0.16, 1)) -> int:
        """Create a physical box, using full dimensions in metres."""
        half = (np.asarray(size) / 2).tolist()
        collision = p.createCollisionShape(p.GEOM_BOX, halfExtents=half, physicsClientId=self.cid)
        visual = p.createVisualShape(p.GEOM_BOX, halfExtents=half, rgbaColor=color, physicsClientId=self.cid)
        body = p.createMultiBody(mass, collision, visual, position,
                                p.getQuaternionFromEuler([0, 0, np.deg2rad(yaw_deg)]), physicsClientId=self.cid)
        p.changeDynamics(body, -1, lateralFriction=self.sim["friction"], restitution=0,
                         linearDamping=self.sim["linear_damping"], angularDamping=self.sim["angular_damping"],
                         collisionMargin=self.sim["collision_margin_m"], frictionAnchor=1,
                         physicsClientId=self.cid)
        if mass > 0 and self.sim.get("enable_sleeping", False):
            p.changeDynamics(body, -1, activationState=p.ACTIVATION_STATE_ENABLE_SLEEPING,
                             physicsClientId=self.cid)
        return body

    def spawn_brick(self, name: str, position: list[float] | None = None,
                    yaw_deg: float = 0, length_scale: float = 1, brick_type: str = "full") -> int:
        """Add stock; return its body ID for known-instance segmentation."""
        if name in self.bricks:
            raise ValueError(f"Brick already exists: {name}")
        spec = self.config["geometry"]["brick_types"].get(brick_type, {})
        if brick_type not in {"full", *self.config["geometry"]["brick_types"]}:
            raise ValueError(f"Unknown brick type: {brick_type}")
        size = list(spec.get("size", self.config["geometry"]["brick_size"]))
        size[0] *= length_scale
        body = self.box(size, position or spec.get("supply", self.sim["supply"]), spec.get("mass", self.sim["brick_mass"]), yaw_deg)
        self.bricks[name] = body
        self.known_ids.add(body)
        return body

    def step(self, count: int = 1) -> None:
        """Advance a fixed number of physics ticks and notify sensors/recorders."""
        for _ in range(count):
            p.stepSimulation(physicsClientId=self.cid)
            self.t += self.sim["dt"]
            for callback in tuple(self.on_step):
                callback()

    def close(self) -> None:
        """Release only this world's resources."""
        if p.isConnected(self.cid):
            p.disconnect(self.cid)

    def __enter__(self) -> "World":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
