"""Panda motion primitives; no agent can directly invoke joint commands."""
from collections.abc import Callable
import numpy as np
import pybullet as p
from masonmind.sim.env import World


class Panda:
    """A seven-joint arm with contact-checked fixed-constraint grasping."""

    def __init__(self, world: World) -> None:
        if world.robot is None:
            raise ValueError("World has no robot")
        self.world, self.body = world, world.robot
        self.joints = list(range(7))
        self.ee = 11
        self.constraint: int | None = None
        self.held: int | None = None
        self.hold_transform: tuple | None = None
        self.phase = "idle"
        self.pick_body: int | None = None
        self.release_hook: Callable[[int], None] | None = None
        self.drop_early = False
        self.orientation_override: tuple | None = None
        self.plan: Callable[[np.ndarray, np.ndarray], list[np.ndarray]] | None = None
        self.execute: Callable[[list[np.ndarray]], None] | None = None
        info = [p.getJointInfo(self.body, j, physicsClientId=world.cid) for j in self.joints]
        self.lower = np.array([j[8] for j in info])
        self.upper = np.array([j[9] for j in info])
        for j, q in zip(self.joints, world.sim["home"]):
            p.resetJointState(self.body, j, q, physicsClientId=world.cid)
        self.command(np.asarray(world.sim["home"]))
        self.fingers(0.04)
        world.step(world.sim["settle_steps"])

    def state(self) -> np.ndarray:
        """Read arm encoders."""
        return np.array([s[0] for s in p.getJointStates(self.body, self.joints, physicsClientId=self.world.cid)])

    def command(self, joints: np.ndarray) -> None:
        """Set motor position targets, preserving physical integration."""
        p.setJointMotorControlArray(self.body, self.joints, p.POSITION_CONTROL,
            targetPositions=joints.tolist(), forces=[self.world.sim["motor_force"]] * 7,
            physicsClientId=self.world.cid)

    def fingers(self, opening: float) -> None:
        """Set the two prismatic finger targets."""
        p.setJointMotorControlArray(self.body, [9, 10], p.POSITION_CONTROL,
                                   targetPositions=[opening, opening], forces=[20, 20], physicsClientId=self.world.cid)

    def pose(self) -> tuple:
        """Read forward kinematics of the grasp target link."""
        state = p.getLinkState(self.body, self.ee, computeForwardKinematics=True, physicsClientId=self.world.cid)
        return state[4], state[5]

    def ik(self, position: list[float], yaw_deg: float = 0) -> np.ndarray:
        """Solve and validate Cartesian IK without changing the live state."""
        orientation = self.orientation_override or p.getQuaternionFromEuler([np.pi, 0, np.deg2rad(yaw_deg)])
        saved = [p.getJointState(self.body, j, physicsClientId=self.world.cid)[:2] for j in self.joints]
        result = None
        try:
            # The current configuration can trap the local solver even at the
            # repeatedly reached supply pose. Retry from a deterministic home
            # seed, then let the normal collision planner connect the live arm
            # to the accepted solution. Never execute the temporary seed state.
            for seed in ([q for q, _ in saved], self.world.sim["home"]):
                for j, value in zip(self.joints, seed):
                    p.resetJointState(self.body, j, value, physicsClientId=self.world.cid)
                for _ in range(4):
                    result = np.array(p.calculateInverseKinematics(self.body, self.ee, position, orientation,
                        maxNumIterations=200, residualThreshold=1e-5, physicsClientId=self.world.cid)[:7])
                    for j, value in zip(self.joints, result):
                        p.resetJointState(self.body, j, value, physicsClientId=self.world.cid)
                actual, _ = self.pose()
                error = float(np.linalg.norm(np.asarray(actual) - position))
                if (error <= self.world.sim["ik_tolerance"] and
                        np.all(result >= self.lower) and np.all(result <= self.upper)):
                    return result
            raise RuntimeError(f"IK infeasible after current/home seeds at {position}: translation residual {error:.4f} m")
        finally:
            for j, (q, velocity) in zip(self.joints, saved):
                p.resetJointState(self.body, j, q, velocity, physicsClientId=self.world.cid)

    def move(self, position: list[float], yaw_deg: float = 0) -> None:
        """Plan to an IK target, then execute the joint-space path."""
        start, goal = self.state(), self.ik(position, yaw_deg)
        path = self.plan(start, goal) if self.plan else [start, goal]
        self.execute_path(path)

    def execute_path(self, path: list[np.ndarray]) -> None:
        """Execute a joint path using configured motor limits."""
        if self.execute:
            self.execute(path)
            return
        dt = self.world.sim["dt"]
        velocity = self.world.config["planning"]["velocity"]
        for a, b in zip(path, path[1:]):
            steps = max(1, int(np.ceil(np.max(np.abs(b - a)) / (velocity * dt))))
            for u in np.linspace(0, 1, steps + 1)[1:]:
                self.command(a + u * (b - a))
                self.world.step()
        self.world.step(self.world.sim["settle_steps"])

    def vertical_move(self, position: list[float], yaw_deg: float = 0) -> None:
        """Constrain the final approach to a sampled Cartesian vertical line."""
        start_position = np.asarray(self.pose()[0])
        distance = np.linalg.norm(np.asarray(position) - start_position)
        count = max(1, int(np.ceil(distance / self.world.sim["cartesian_step_m"])))
        path = [self.state()]
        for fraction in np.linspace(0, 1, count + 1)[1:]:
            target = start_position + fraction * (np.asarray(position) - start_position)
            goal = self.ik(target.tolist(), yaw_deg)
            segment = self.plan(path[-1], goal) if self.plan else [path[-1], goal]
            path.extend(segment[1:])
        self.execute_path(path)

    def grasp(self, body: int) -> None:
        """Attach only when a finger has reached the brick."""
        self.fingers(0)
        self.world.step(self.world.sim["settle_steps"])
        contacts = p.getContactPoints(self.body, body, physicsClientId=self.world.cid)
        if not any(c[3] in (9, 10) for c in contacts):
            raise RuntimeError("Grasp failed: no finger contact with the requested brick")
        parent_pos, parent_orn = self.pose()
        child_pos, child_orn = p.getBasePositionAndOrientation(body, physicsClientId=self.world.cid)
        inverse = p.invertTransform(parent_pos, parent_orn)
        rel_pos, rel_orn = p.multiplyTransforms(*inverse, child_pos, child_orn)
        self.constraint = p.createConstraint(self.body, self.ee, body, -1, p.JOINT_FIXED,
            [0, 0, 0], rel_pos, [0, 0, 0], parentFrameOrientation=rel_orn,
            physicsClientId=self.world.cid)
        self.hold_transform = (rel_pos, rel_orn)
        self.held = body

    def release(self) -> None:
        """Detach and open the gripper; allow gravity to settle the brick."""
        if self.constraint is None:
            raise RuntimeError("Cannot release: gripper is empty")
        p.removeConstraint(self.constraint, physicsClientId=self.world.cid)
        released_body = self.held
        self.constraint = self.held = self.hold_transform = None
        self.fingers(0.04)
        self.world.step(self.world.sim["settle_steps"])
        if self.release_hook:
            self.release_hook(released_body)
            self.world.step(self.world.sim["settle_steps"])

    def pick_place(self, body: int, source: list[float], target: list[float], yaw_deg: float = 0,
                   source_yaw_deg: float = 0) -> None:
        """Approach, descend, grasp, lift, transfer, place, release and retreat."""
        offset = self.world.sim["grasp_offset"]
        height = self.world.sim["approach_height"]
        pick = np.asarray(source, dtype=float) + [0, 0, offset]
        place = np.asarray(target, dtype=float) + [0, 0, offset]
        self.pick_body = body
        self.orientation_override = None
        self.phase = "approach"
        self.fingers(0.04)
        self.move((pick + [0, 0, height]).tolist(), source_yaw_deg)
        self.phase = "pick"
        self.vertical_move(pick.tolist(), source_yaw_deg)
        self.grasp(body)
        desired = p.getQuaternionFromEuler([0, 0, np.deg2rad(yaw_deg)])
        # Release above the planned surface when configured, avoiding pressure
        # on settled lower courses while the brick is still constrained.
        release_target = (np.asarray(target) + [0, 0, self.world.sim.get("placement_clearance_m", 0.0)]).tolist()
        parent_position, parent_orientation = p.multiplyTransforms(release_target, desired, *p.invertTransform(*self.hold_transform))
        self.phase = "lift"
        self.vertical_move((pick + [0, 0, height]).tolist(), source_yaw_deg)
        if self.drop_early:
            self.release()
            self.drop_early = False
            self.phase = "idle"
            self.pick_body = None
            return
        self.phase = "transfer"
        place = np.asarray(parent_position)
        self.orientation_override = parent_orientation
        self.move((place + [0, 0, height]).tolist(), yaw_deg)
        self.phase = "place"
        self.vertical_move(place.tolist(), yaw_deg)
        self.release()
        self.phase = "retreat"
        self.vertical_move((place + [0, 0, height]).tolist(), yaw_deg)
        self.phase = "idle"
        self.pick_body = None
