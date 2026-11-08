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
    length, width, _ = brick.size or size
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
        bounds = rotate(unary_union(list(polygons.values())), -cfg.get("wall_yaw_deg", 0), origin=(0, 0)).bounds
        extent = bounds[2] - bounds[0]
        if design.program.arc_angle_deg or design.program.plan_curvature is not None:
            pitch = cfg["pitch"] if design.program.gap_ratio is None else cfg["brick_size"][0] * (1 + design.program.gap_ratio)
            centreline_length = (design.program.bricks_per_course - 1) * pitch
            extent = max(extent, centreline_length + cfg["brick_size"][0])
        height = max(b.z for b in active) + cfg["brick_size"][2] / 2 - cfg["origin"][2]
        depth = bounds[3] - bounds[1]
        dimensions = f"Actual length/height/depth {extent:.4f}/{height:.4f}/{depth:.4f} m; allowed {cfg['max_length']:.4f}/{cfg['max_height']:.4f}/{cfg['max_depth']:.4f} m; radial reach limit {cfg['workspace_radius']:.4f} m."
        if depth > cfg["max_depth"]:
            issue("dimensions", [], dimensions, depth, cfg["max_depth"])
        if extent > cfg["max_length"]:
            issue("dimensions", [], "Wall length exceeds limit. " + dimensions, extent, cfg["max_length"])
        if height > cfg["max_height"]:
            issue("dimensions", [], "Wall height exceeds limit. " + dimensions, height, cfg["max_height"])
    for brick in active:
        poly = polygons[brick.id]
        reach = max(np.linalg.norm([x, y, brick.z]) for x, y in poly.exterior.coords)
        expected_z = cfg["origin"][2] + (brick.course + 0.5) * cfg["brick_size"][2]
        if reach > cfg["workspace_radius"] or abs(brick.z - expected_z) > cfg["support_epsilon"]:
            issue("dimensions", [brick.id], f"{brick.id}: outside workspace or course elevation; radius {reach:.4f} m. " + dimensions, float(reach), cfg["workspace_radius"])
        if brick.course == 0:
            continue
        below = unary_union([polygons[b.id] for b in courses.get(brick.course - 1, [])])
        supporting = [b for b in courses.get(brick.course - 1, [])
                      if poly.intersection(polygons[b.id]).area > cfg["overlap_tolerance_m2"]]
        if len(supporting) < design.program.required_supports:
            issue("support", [brick.id],
                  f"{brick.id}: contacts {len(supporting)} supporting bricks; requires {design.program.required_supports}",
                  len(supporting), design.program.required_supports)
        support = poly.intersection(below)
        ratio = support.area / poly.area
        local = rotate(translate(support, -brick.x, -brick.y), -brick.yaw_deg, origin=(0, 0))
        length, width, _ = brick.size or cfg["brick_size"]
        left = local.intersection(box(-length, -width, 0, width)).area / poly.area
        right = local.intersection(box(0, -width, length, width)).area / poly.area
        epsilon = cfg["support_epsilon"]
        bridge = (left + epsilon >= cfg["min_contact_per_side"] and right + epsilon >= cfg["min_contact_per_side"]
                  and ratio + epsilon >= cfg["min_bridge_support_ratio"] and len(supporting) >= 2)
        if ratio + epsilon < cfg["min_support_ratio"] and not bridge:
            issue("support", [brick.id], f"{brick.id}: support {ratio:.3f}; sides {left:.3f}/{right:.3f}; need total {cfg['min_support_ratio']:.3f} or bridge total {cfg['min_bridge_support_ratio']:.3f} with each side {cfg['min_contact_per_side']:.3f}", ratio, cfg["min_bridge_support_ratio"])
        contacts = [poly.intersection(polygons[b.id]) for b in supporting]
        hull = unary_union(contacts).convex_hull
        safe_hull = hull.buffer(-cfg["com_margin_m"]) if cfg["com_margin_m"] else hull
        if safe_hull.is_empty or not safe_hull.buffer(epsilon).covers(Point(brick.x, brick.y)):
            issue("com", [brick.id], f"{brick.id}: projected center of mass outside contact hull with margin {cfg['com_margin_m']} m")
    for course in courses.values():
        for a, b in combinations(course, 2):
            area = polygons[a.id].intersection(polygons[b.id]).area
            if area > cfg["overlap_tolerance_m2"]:
                issue("overlap", [a.id, b.id], f"{a.id} intersects {b.id}: overlap area {area:.8f} m^2", area, cfg["overlap_tolerance_m2"])
    return VerificationReport(passed=not issues, issues=issues)
