"""PyBullet configuration checking with attached-object transforms."""
import numpy as np
import pybullet as p
from masonmind.sim.robot import Panda


class CollisionChecker:
    """Check a robot against explicitly supplied known or perceived bodies.

    This class never enumerates the world to discover unknown obstacles. The caller
    must supply perception proxies. Candidate checks restore all encoder/body states.
    """

    def __init__(self, arm: Panda, scene: list[int]):
        self.arm, self.world = arm, arm.world
        self.scene = list(scene)
        self.support_bodies: set[int] = {self.world.table}
        self.margin = self.world.config["planning"]["safety_margin"]
        self.parents = {j: p.getJointInfo(arm.body, j, physicsClientId=self.world.cid)[16]
                        for j in range(p.getNumJoints(arm.body, physicsClientId=self.world.cid))}
        self.joint_types = {j: p.getJointInfo(arm.body, j, physicsClientId=self.world.cid)[2] for j in self.parents}
        self.last_collision: str | None = None

    def adjacent(self, a: int, b: int) -> bool:
        """Exclude adjacent links and links separated only by short fixed chains."""
        def connected(child: int, ancestor: int) -> bool:
            movable = 0
            while child in self.parents:
                movable += self.joint_types[child] != p.JOINT_FIXED
                child = self.parents[child]
                if child == ancestor:
                    return movable <= 1
                if movable > 1:
                    return False
            return False
        return connected(a, b) or connected(b, a) or {a, b} <= {8, 9, 10, 11}

    def __call__(self, q: np.ndarray) -> bool:
        """Return True only for an admissible collision-free configuration."""
        arm, cid = self.arm, self.world.cid
        if np.any(q < arm.lower) or np.any(q > arm.upper):
            self.last_collision = "joint limits"
            return False
        joints = [p.getJointState(arm.body, j, physicsClientId=cid)[:2] for j in arm.joints]
        held_pose = p.getBasePositionAndOrientation(arm.held, physicsClientId=cid) if arm.held is not None else None
        held_velocity = p.getBaseVelocity(arm.held, physicsClientId=cid) if arm.held is not None else None
        try:
            for j, value in zip(arm.joints, q):
                p.resetJointState(arm.body, j, value, physicsClientId=cid)
            if held_pose is not None:
                pose = p.multiplyTransforms(*arm.pose(), *arm.hold_transform)
                p.resetBasePositionAndOrientation(arm.held, *pose, physicsClientId=cid)
            for contact in p.getClosestPoints(arm.body, arm.body, self.margin, physicsClientId=cid):
                if contact[3] != contact[4] and not self.adjacent(contact[3], contact[4]):
                    self.last_collision = f"self links {contact[3]}/{contact[4]}"
                    return False
            if arm.held is not None:
                for contact in p.getClosestPoints(arm.body, arm.held, self.margin, physicsClientId=cid):
                    if contact[3] not in {8, 9, 10, 11}:
                        self.last_collision = f"held brick/robot link {contact[3]}"
                        return False
            for obstacle in self.scene:
                if obstacle == arm.held:
                    continue
                contacts = p.getClosestPoints(arm.body, obstacle, self.margin, physicsClientId=cid)
                contacts = [c for c in contacts if c[3] >= 0 and not
                            (obstacle == arm.pick_body and arm.phase in {"pick", "retreat"} and c[3] in {9, 10})]
                if contacts:
                    self.last_collision = f"robot/scene body {obstacle}"
                    return False
                if arm.held is not None:
                    held_contacts = p.getClosestPoints(arm.held, obstacle, self.margin, physicsClientId=cid)
                    for contact in held_contacts:
                        permitted = (obstacle in self.support_bodies and arm.phase in {"lift", "place"}
                                     and contact[8] >= -self.world.config["planning"]["contact_penetration_m"]
                                     and contact[7][2] > 0.9)
                        # A vertical extraction may start inside the clearance margin
                        # next to a misplaced neighbour, but never through penetration.
                        permitted |= (obstacle in self.support_bodies and arm.phase == "lift"
                                      and contact[8] >= -self.world.config["planning"]["proxy_fit_uncertainty_m"])
                        if not permitted:
                            self.last_collision = f"held brick/scene body {obstacle}, gap={contact[8]:.6f}, normal={contact[7]}, support={obstacle in self.support_bodies}"
                            return False
            self.last_collision = None
            return True
        finally:
            for j, (value, velocity) in zip(arm.joints, joints):
                p.resetJointState(arm.body, j, value, velocity, physicsClientId=cid)
            if held_pose is not None:
                p.resetBasePositionAndOrientation(arm.held, *held_pose, physicsClientId=cid)
                p.resetBaseVelocity(arm.held, *held_velocity, physicsClientId=cid)
