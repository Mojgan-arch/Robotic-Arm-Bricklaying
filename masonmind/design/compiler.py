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
                          "twist_gradient": 2 * t - 1, "alternate": (-1) ** (course + index), "constant": 1}[rule.kind]
                value = rule.amplitude_deg * factor
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
                elif rule.kind == "checker":
                    match = (course + index) % 2 == 0 and rng.random() < 2 * ratio
                else:
                    match = rng.random() < ratio
                opening |= match
            offset = length / 2 if program.bond == "running" and course % 2 else 0
            bricks.append(Brick(id=f"c{course:02}_b{index:02}", course=course, index=index,
                                x=ox + (index - (program.bricks_per_course - 1) / 2) * cfg["pitch"] + offset,
                                y=oy, z=oz + (course + 0.5) * height, yaw_deg=yaw, is_opening=opening))
    return Design(program=program, bricks=bricks)
