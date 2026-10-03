"""Per-brick ICP using only visible depth samples and nominal BIM geometry."""
from dataclasses import dataclass
import numpy as np
from masonmind.schemas import Brick, BrickDeviation
from masonmind.sim.camera import Frame


@dataclass
class ScanResult:
    deviations: list[BrickDeviation]
    poses: dict[str, tuple[list[float], float]]
    broken_ids: list[str]
    diagnostics: dict


def model_cloud(brick: Brick, size: list[float], spacing: float):
    """Deterministically sample all six faces, retaining analytic face normals."""
    import open3d as o3d
    points, normals = [], []
    for axis in range(3):
        other = [j for j in range(3) if j != axis]
        grids = [np.linspace(-size[j] / 2, size[j] / 2, int(np.ceil(size[j] / spacing)) + 1) for j in other]
        for sign in (-1, 1):
            for u in grids[0]:
                for v in grids[1]:
                    point, normal = np.zeros(3), np.zeros(3)
                    point[axis], point[other[0]], point[other[1]] = sign * size[axis] / 2, u, v
                    normal[axis] = sign
                    points.append(point)
                    normals.append(normal)
    yaw = np.deg2rad(brick.yaw_deg)
    rotation = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(np.asarray(points) @ rotation.T + [brick.x, brick.y, brick.z])
    cloud.normals = o3d.utility.Vector3dVector(np.asarray(normals) @ rotation.T)
    return cloud


def scan_bricks(frames: list[Frame], bricks: list[Brick], instance_ids: dict[str, int], config: dict) -> ScanResult:
    """Fit observed points to nominal surfaces; invert ICP to recover as-built pose."""
    import open3d as o3d
    cfg, size = config["scan"], config["geometry"]["brick_size"]
    deviations, poses, broken, diagnostics = [], {}, [], {}
    for brick in bricks:
        if brick.is_opening:
            continue
        body = instance_ids.get(brick.id, -2)
        parts = [frame.points[frame.body_ids == body] for frame in frames]
        points = np.concatenate(parts) if parts else np.empty((0, 3))
        if len(points) < cfg["min_brick_points"]:
            deviations.append(BrickDeviation(brick_id=brick.id, dx_mm=0, dy_mm=0, dz_mm=0,
                                             dyaw_deg=0, missing=True, within_tolerance=False))
            diagnostics[brick.id] = {"points": len(points), "status": "unobserved: absent or occluded"}
            continue
        observed = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
        model = model_cloud(brick, size, cfg["model_spacing_m"])
        registration = o3d.pipelines.registration.registration_icp(observed, model, cfg["icp_max_distance"],
            np.eye(4), o3d.pipelines.registration.TransformationEstimationPointToPlane(),
            o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=cfg["icp_iterations"]))
        transform = np.linalg.inv(registration.transformation)
        nominal = np.array([brick.x, brick.y, brick.z, 1])
        position = (transform @ nominal)[:3]
        dyaw = float(np.rad2deg(np.arctan2(transform[1, 0], transform[0, 0])))
        delta = (position - nominal[:3]) * 1000
        yaw = brick.yaw_deg + dyaw
        rotation = np.array([[np.cos(np.deg2rad(yaw)), -np.sin(np.deg2rad(yaw))],
                             [np.sin(np.deg2rad(yaw)), np.cos(np.deg2rad(yaw))]])
        local = (points[:, :2] - position[:2]) @ rotation
        length = float(np.ptp(local[:, 0]))
        damaged = length < cfg["broken_length_ratio"] * size[0]
        if damaged:
            broken.append(brick.id)
        reliable = registration.fitness >= cfg["icp_min_fitness"]
        good = reliable and not damaged and np.linalg.norm(delta) <= cfg["translation_tolerance_mm"] and abs(dyaw) <= cfg["yaw_tolerance_deg"]
        deviations.append(BrickDeviation(brick_id=brick.id, dx_mm=float(delta[0]), dy_mm=float(delta[1]), dz_mm=float(delta[2]),
                                         dyaw_deg=dyaw, missing=False, within_tolerance=bool(good)))
        poses[brick.id] = (position.tolist(), yaw)
        diagnostics[brick.id] = {"points": len(points), "fitness": registration.fitness,
                                "rmse_m": registration.inlier_rmse, "observed_length_m": length, "reliable": reliable}
    return ScanResult(deviations, poses, broken, diagnostics)
