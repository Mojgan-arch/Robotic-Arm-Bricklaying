"""Precedence-aware course sequencing with gripper and physics checks."""
from collections.abc import Callable
from typing import Any
from shapely.affinity import rotate, translate
from shapely.geometry import box
from masonmind.config import load_config
from masonmind.design.verifier import footprint
from masonmind.schemas import (Design, Brick, SequenceStrategy, AssemblySequence,
                               VerificationIssue, VerificationReport, StabilityReport)


def dependencies(design: Design, config: dict[str, Any] | None = None) -> dict[str, set[str]]:
    """Every positive-area supporting brick is a mandatory predecessor."""
    cfg = (config or load_config())["geometry"]
    active = [b for b in design.bricks if not b.is_opening]
    polys = {b.id: footprint(b, cfg["brick_size"]) for b in active}
    return {b.id: {a.id for a in active if a.course == b.course - 1 and
                  polys[b.id].intersection(polys[a.id]).area > cfg["overlap_tolerance_m2"]} for b in active}


def gripper_collisions(target: Brick, placed: list[Brick], config: dict[str, Any]) -> list[str]:
    """Sweep two open finger boxes vertically past already placed brick volumes."""
    cfg = config["sequence"]
    size = config["geometry"]["brick_size"]
    result = set()
    for side in (-1, 1):
        offset = side * (cfg["finger_gap"] + cfg["finger_thickness"]) / 2
        poly = box(-cfg["finger_length"] / 2, offset - cfg["finger_thickness"] / 2,
                   cfg["finger_length"] / 2, offset + cfg["finger_thickness"] / 2)
        poly = translate(rotate(poly, target.yaw_deg, origin=(0, 0)), target.x, target.y)
        for brick in placed:
            if brick.z + size[2] / 2 <= target.z - size[2] / 2 + config["geometry"]["support_epsilon"]:
                continue
            if brick.z - size[2] / 2 > target.z + cfg["approach_height"]:
                continue
            if poly.intersection(footprint(brick, size)).area > config["geometry"]["overlap_tolerance_m2"]:
                result.add(brick.id)
    return sorted(result)


def plan_sequence(design: Design, strategy: SequenceStrategy, config: dict[str, Any] | None = None,
                  stability: Callable[[list[Brick], dict], StabilityReport] | None = None) -> AssemblySequence:
    """Topologically sort by strategy, then check every approach and checkpoint."""
    cfg = config or load_config()
    if stability is None:
        from masonmind.sim.stability import drop_test
        stability = drop_test
    active = {b.id: b for b in design.bricks if not b.is_opening}
    graph = dependencies(design, cfg)
    center = (design.program.bricks_per_course - 1) / 2
    keys = {"left_to_right": lambda b: b.index, "right_to_left": lambda b: -b.index,
            "center_out": lambda b: abs(b.index - center), "ends_in": lambda b: -abs(b.index - center),
            "high_rotation_first": lambda b: -abs(b.yaw_deg), "low_rotation_first": lambda b: abs(b.yaw_deg)}
    order, placed, done, issues = [], [], set(), []
    while len(done) < len(active):
        ready = [b for b in active.values() if b.id not in done and graph[b.id] <= done]
        if not ready:
            issues.append(VerificationIssue(check="sequence_precedence", brick_ids=sorted(set(active) - done),
                                             message="Precedence graph has no available placement"))
            break
        brick = min(ready, key=lambda b: (b.course, keys[strategy.strategy](b), b.index))
        collisions = gripper_collisions(brick, placed, cfg)
        if collisions:
            issues.append(VerificationIssue(check="gripper_clearance", brick_ids=[brick.id, *collisions],
                                             message=f"Vertical open-gripper approach to {brick.id} intersects {collisions}"))
        order.append(brick.id)
        placed.append(brick)
        done.add(brick.id)
        end_course = not any(b.course == brick.course and b.id not in done for b in active.values())
        if end_course or len(done) % cfg["sequence"]["checkpoint_every"] == 0:
            report = stability(placed, cfg)
            if not report.passed:
                issues.append(VerificationIssue(check="intermediate_stability", brick_ids=report.unstable_ids,
                    message=f"After {len(done)} placements: displacement {report.max_translation_m:.5f} m, rotation {report.max_angle_deg:.2f} deg",
                    value=report.max_translation_m, limit=cfg["sim"]["stability_translation_m"]))
    return AssemblySequence(order=order, strategy=strategy, report=VerificationReport(passed=not issues, issues=issues))
