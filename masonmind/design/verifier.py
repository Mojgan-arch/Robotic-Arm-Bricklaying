"""Footprint-based dimensional, collision and local-support checks."""
from itertools import combinations
from typing import Any
import numpy as np
from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, Point, box
from shapely.ops import unary_union
from masonmind.config import load_config
from masonmind.schemas import Brick, Design, VerificationIssue, VerificationReport


def footprint(brick: Brick, size: list[float]) -> Polygon:
    """Return an oriented horizontal footprint in metres."""
    length, width, _ = size
    return translate(rotate(box(-length / 2, -width / 2, length / 2, width / 2),
                            brick.yaw_deg, origin=(0, 0)), brick.x, brick.y)


def verify(design: Design, config: dict[str, Any] | None = None) -> VerificationReport:
    """Reject local geometric failures; global physics is a separate check."""
    cfg = (config or load_config())["geometry"]
    active = [b for b in design.bricks if not b.is_opening]
    issues: list[VerificationIssue] = []
    polygons = {b.id: footprint(b, cfg["brick_size"]) for b in active}
    courses = {c: [b for b in active if b.course == c] for c in range(design.program.n_courses)}

    def issue(check: str, ids: list[str], message: str, value: float | None = None, limit: float | None = None) -> None:
        issues.append(VerificationIssue(check=check, brick_ids=ids, message=message, value=value, limit=limit))

    if not active:
        issue("dimensions", [], "Design contains no solid bricks")
    else:
        bounds = unary_union(list(polygons.values())).bounds
        extent = bounds[2] - bounds[0]
        height = max(b.z for b in active) + cfg["brick_size"][2] / 2 - cfg["origin"][2]
        if extent > cfg["max_length"]:
            issue("dimensions", [], f"Wall length {extent:.4f} m exceeds {cfg['max_length']:.4f} m", extent, cfg["max_length"])
        if height > cfg["max_height"]:
            issue("dimensions", [], f"Wall height {height:.4f} m exceeds {cfg['max_height']:.4f} m", height, cfg["max_height"])
    for brick in active:
        poly = polygons[brick.id]
        reach = max(np.linalg.norm([x, y, brick.z]) for x, y in poly.exterior.coords)
        expected_z = cfg["origin"][2] + (brick.course + 0.5) * cfg["brick_size"][2]
        if reach > cfg["workspace_radius"] or abs(brick.z - expected_z) > cfg["support_epsilon"]:
            issue("dimensions", [brick.id], f"{brick.id}: outside workspace or course elevation; radius {reach:.4f} m", float(reach), cfg["workspace_radius"])
        if brick.course == 0:
            continue
        below = unary_union([polygons[b.id] for b in courses.get(brick.course - 1, [])])
        support = poly.intersection(below)
        ratio = support.area / poly.area
        if ratio + cfg["support_epsilon"] < cfg["min_support_ratio"]:
            issue("support", [brick.id], f"{brick.id}: supported footprint {ratio:.3f} < required {cfg['min_support_ratio']:.3f}", ratio, cfg["min_support_ratio"])
        if not support.buffer(cfg["support_epsilon"]).covers(Point(brick.x, brick.y)):
            issue("com", [brick.id], f"{brick.id}: projected center of mass lies outside supporting area")
    for course in courses.values():
        for a, b in combinations(course, 2):
            area = polygons[a.id].intersection(polygons[b.id]).area
            if area > cfg["overlap_tolerance_m2"]:
                issue("overlap", [a.id, b.id], f"{a.id} intersects {b.id}: overlap area {area:.8f} m^2", area, cfg["overlap_tolerance_m2"])
    return VerificationReport(passed=not issues, issues=issues)
