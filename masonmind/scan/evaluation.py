"""Ground-truth evaluation only. Never feed this output into control decisions."""
import numpy as np
import pybullet as p
from masonmind.schemas import Brick, BrickDeviation


def evaluate_scan(bricks: list[Brick], deviations: list[BrickDeviation], ids: dict[str, int], client: int) -> dict:
    """Compare estimated translations/yaws against simulation truth after sensing."""
    measured = {d.brick_id: d for d in deviations}
    records = []
    for brick in bricks:
        if brick.is_opening or brick.id not in ids or brick.id not in measured:
            continue
        deviation = measured[brick.id]
        if deviation.missing:
            continue
        position, orientation = p.getBasePositionAndOrientation(ids[brick.id], physicsClientId=client)
        delta = (np.asarray(position) - [brick.x, brick.y, brick.z]) * 1000
        yaw = (np.rad2deg(p.getEulerFromQuaternion(orientation)[2]) - brick.yaw_deg + 180) % 360 - 180
        error = np.linalg.norm(delta - [deviation.dx_mm, deviation.dy_mm, deviation.dz_mm])
        yaw_error = abs((yaw - deviation.dyaw_deg + 180) % 360 - 180)
        records.append({"brick_id": brick.id, "truth_delta_mm": delta.tolist(), "truth_dyaw_deg": yaw,
                        "translation_error_mm": float(error), "yaw_error_deg": float(yaw_error)})
    return {"records": records, "observed_count": len(records),
            "mean_translation_error_mm": float(np.mean([r["translation_error_mm"] for r in records])) if records else None,
            "mean_yaw_error_deg": float(np.mean([r["yaw_error_deg"] for r in records])) if records else None}
