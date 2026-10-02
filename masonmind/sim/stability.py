"""Independent dynamic drop tests; never mutate the construction world."""
from typing import Any
import numpy as np
import pybullet as p
from masonmind.schemas import Brick, StabilityReport
from masonmind.sim.env import World
from masonmind.config import load_config


def drop_test(bricks: list[Brick], config: dict[str, Any] | None = None) -> StabilityReport:
    """Measure movement and rotation after a configured gravity settling interval."""
    cfg = config or load_config()
    translations, angles, unstable = [], [], []
    isolated = {**cfg, "sim": {**cfg["sim"], "gui": False}}
    with World(isolated, robot=False) as world:
        active = [b for b in bricks if not b.is_opening]
        for brick in active:
            world.spawn_brick(brick.id, [brick.x, brick.y, brick.z], brick.yaw_deg)
        world.step(cfg["sim"]["stability_steps"])
        for brick in active:
            pos, orn = p.getBasePositionAndOrientation(world.bricks[brick.id], physicsClientId=world.cid)
            translation = float(np.linalg.norm(np.asarray(pos) - [brick.x, brick.y, brick.z]))
            expected = p.getQuaternionFromEuler([0, 0, np.deg2rad(brick.yaw_deg)])
            angle = float(np.rad2deg(2 * np.arccos(np.clip(abs(np.dot(orn, expected)), 0, 1))))
            translations.append(translation)
            angles.append(angle)
            if translation > cfg["sim"]["stability_translation_m"] or angle > cfg["sim"]["stability_angle_deg"]:
                unstable.append(brick.id)
    return StabilityReport(passed=not unstable, max_translation_m=max(translations, default=0),
                           max_angle_deg=max(angles, default=0), unstable_ids=unstable)
