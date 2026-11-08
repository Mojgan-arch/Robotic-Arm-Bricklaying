"""Seeded design DSL interpreter with no model-generated coordinates."""
import math
from typing import Any
import numpy as np
from masonmind.config import load_config
from masonmind.schemas import Brick, Design, DesignProgram, Region


def region_coordinates(region: Region, course: int, u: float) -> tuple[float, float] | None:
    """Map an included cell to normalized coordinates in its rule region."""
    if not (region.course_start <= course <= region.course_end and region.u_start <= u <= region.u_end):
        return None
    return ((u - region.u_start) / (region.u_end - region.u_start),
            (course - region.course_start) / max(1, region.course_end - region.course_start))


def compile_program(program: DesignProgram, config: dict[str, Any] | None = None) -> Design:
    """Expand ordered pattern rules to exact world-frame poses, deterministically."""
    cfg = (config or load_config())["geometry"]
    length, _, height = cfg["brick_size"]
    ox, oy, oz = cfg["origin"]
    wall_yaw = cfg.get("wall_yaw_deg", 0.0)
    angle = math.radians(wall_yaw)
    pitch = cfg["pitch"] if program.gap_ratio is None else length * (1 + program.gap_ratio)
    span = (program.bricks_per_course - 1) * pitch
    curvature = 1 / program.plan_curvature if program.plan_curvature is not None else math.radians(program.arc_angle_deg) / span
    rng = np.random.default_rng(program.seed)
    bricks = []
    for course in range(program.n_courses):
        for index in range(program.bricks_per_course):
            u = (index + 0.5) / program.bricks_per_course
            yaw, opening = 0.0, False
            for rule in program.rotation_rules:
                coords = region_coordinates(rule.region, course, u)
                if coords is None:
                    continue
                v, t = coords
                phase = 2 * math.pi * rule.frequency * v + rule.phase_shift_per_course * (course - rule.region.course_start)
                factor = {"wave": math.sin(phase), "zigzag": (2 / math.pi) * math.asin(math.sin(phase)),
                          "length_gradient": 0,
                          "twist_gradient": 2 * t - 1, "alternate": (-1) ** (course + index), "constant": 1}[rule.kind]
                value = rule.amplitude_deg * factor
                if rule.kind == "length_gradient":
                    first = max(0, math.ceil(rule.region.u_start * program.bricks_per_course - 0.5))
                    last = min(program.bricks_per_course - 1, math.floor(rule.region.u_end * program.bricks_per_course - 0.5))
                    fraction = (index - first) / max(1, last - first)
                    value = rule.start_deg + fraction * (rule.amplitude_deg - rule.start_deg)
                yaw = value if rule.combine == "override" else yaw + value
            yaw = float(np.clip(yaw, -cfg["yaw_limit_deg"], cfg["yaw_limit_deg"]))
            for rule in program.opening_rules:
                coords = region_coordinates(rule.region, course, u)
                if coords is None:
                    continue
                v, t = coords
                ratio = rule.ratio_start + t * (rule.ratio_end - rule.ratio_start)
                size = rule.size if rule.size is not None else ratio
                if rule.kind == "diamond":
                    match = abs(v - 0.5) + abs(t - 0.5) <= size / 2
                elif rule.kind == "stripe":
                    match = abs(v - 0.5) <= size / 2
                elif rule.kind == "hit_and_miss":
                    # Explicit gaps space solid bricks; do not also delete alternate solids.
                    match = program.gap_ratio is None and (course + index) % 2 == 1
                elif rule.kind == "checker":
                    match = (course + index) % 2 == 0 and rng.random() < 2 * ratio
                else:
                    match = rng.random() < ratio
                opening |= match
            offset = (length / 2 if program.gap_ratio is None else pitch / 2) if program.bond == "running" and course % 2 else 0
            along = (index - (program.bricks_per_course - 1) / 2) * pitch + offset
            tangent = along * curvature
            local_x = math.sin(tangent) / curvature if abs(curvature) > 1e-10 else along
            local_y = 2 * math.sin(tangent / 2) ** 2 / curvature if abs(curvature) > 1e-10 else 0.0
            bricks.append(Brick(id=f"c{course:02}_b{index:02}", course=course, index=index,
                                x=ox + local_x * math.cos(angle) - local_y * math.sin(angle),
                                y=oy + local_x * math.sin(angle) + local_y * math.cos(angle), z=oz + (course + 0.5) * height,
                                yaw_deg=yaw + wall_yaw + math.degrees(tangent), is_opening=opening))
    if program.bond == "running" and program.end_treatment != "none":
        # Keep the nominal full-course envelope. Offset courses have n-1 full
        # bricks; half treatment closes both ends without extending that envelope.
        for course in range(1, program.n_courses, 2):
            row = [b for b in bricks if b.course == course]
            bricks = [b for b in bricks if not (b.course == course and b.index == program.bricks_per_course - 1)]
            if program.end_treatment == "half_brick":
                half_size = list(cfg["brick_types"]["half"]["size"])
                for side, reference in ((-1, row[0]), (1, row[-1])):
                    along = side * (span / 2 + length / 2 - half_size[0] / 2)
                    tangent = along * curvature
                    local_x = math.sin(tangent) / curvature if abs(curvature) > 1e-10 else along
                    local_y = 2 * math.sin(tangent / 2) ** 2 / curvature if abs(curvature) > 1e-10 else 0
                    old_along = (reference.index - (program.bricks_per_course - 1) / 2) * pitch + (length / 2 if program.gap_ratio is None else pitch / 2)
                    bricks.append(Brick(id=f"c{course:02}_end_{'left' if side < 0 else 'right'}", course=course,
                        index=0 if side < 0 else program.bricks_per_course, brick_type="half", size=half_size,
                        x=ox+local_x*math.cos(angle)-local_y*math.sin(angle),
                        y=oy+local_x*math.sin(angle)+local_y*math.cos(angle),
                        z=oz+course*height+half_size[2]/2,
                        yaw_deg=reference.yaw_deg+math.degrees(tangent-old_along*curvature)))
    return Design(program=program, bricks=bricks)
