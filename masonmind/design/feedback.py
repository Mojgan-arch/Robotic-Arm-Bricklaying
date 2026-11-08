"""Bounded geometric diagnostics; no model-generated poses."""
from collections import defaultdict
from collections.abc import Callable
import json
from masonmind.schemas import DesignProgram, Design, VerificationReport
from masonmind.design.compiler import compile_program
from masonmind.design.verifier import verify


def signature(program: DesignProgram) -> str:
    """Ignore prose and numeric jitter when detecting repeated designs."""
    def normalized(value: object) -> object:
        if isinstance(value, float):
            return round(value, 4)
        if isinstance(value, dict):
            return {k: normalized(v) for k, v in value.items() if k != "rationale"}
        if isinstance(value, list):
            return [normalized(v) for v in value]
        return value
    return json.dumps(normalized(program.model_dump()), sort_keys=True)


def compact_issues(design: Design, report: VerificationReport) -> list[str]:
    """Group failures by type with course/parity coverage and measured ranges."""
    groups = defaultdict(list)
    bricks = {b.id: b for b in design.bricks}
    for issue in report.issues:
        groups[issue.check].append(issue)
    result = []
    for check, issues in groups.items():
        affected = {name for issue in issues for name in issue.brick_ids if name in bricks}
        courses = sorted({bricks[name].course for name in affected})
        parity = sorted({bricks[name].index % 2 for name in affected})
        values = [i.value for i in issues if i.value is not None]
        limits = sorted({i.limit for i in issues if i.limit is not None})
        summary = f"{check}: {len(affected)} bricks, courses {courses}, index parity {parity} (0 even, 1 odd)."
        if values:
            summary += f" Measured range {min(values):.3f}–{max(values):.3f}; thresholds {limits}."
        summary += " Example: " + issues[0].message
        if check == "support":
            pairs = [(abs(bricks[i.brick_ids[0]].yaw_deg), i.value) for i in issues
                     if i.value is not None and "supported" not in i.message and "sides" in i.message and i.brick_ids]
            if len(pairs) > 1:
                pairs.sort()
                split = len(pairs) // 2
                low = sum(v for _, v in pairs[:split]) / split
                high = sum(v for _, v in pairs[split:]) / (len(pairs) - split)
                summary += f" Support at lower/higher world yaw: {low:.3f}/{high:.3f}; " + ("worse at higher yaw." if high < low - .001 else "no worsening trend measured.")
        result.append(summary)
    return result


def repair_hints(program: DesignProgram, config: dict) -> dict:
    """Search geometric feasibility only; report absence rather than invent a fix."""
    def candidate(gap: float | None, angle: float) -> bool:
        data = program.model_dump()
        data["gap_ratio"] = gap
        peak = max([abs(r.start_deg) for r in program.rotation_rules] + [r.amplitude_deg for r in program.rotation_rules] + [0])
        for rule in data["rotation_rules"]:
            rule["amplitude_deg"] = rule["amplitude_deg"] / peak * angle if peak else 0
            rule["start_deg"] = rule["start_deg"] / peak * angle if peak else 0
        return verify(compile_program(DesignProgram.model_validate(data), config), config).passed

    def maximum(upper: float, predicate: Callable[[float], bool]) -> float | None:
        # Sample first because openings/curvature can make feasibility non-monotonic.
        passed = [upper * i / 16 for i in range(17) if predicate(upper * i / 16)]
        if not passed:
            return None
        low = max(passed)
        high = min(upper, low + upper / 16)
        for _ in range(12):
            mid = (low + high) / 2
            if predicate(mid):
                low = mid
            else:
                high = mid
        return round(low, 5)

    fitting = []
    for count in range(4, 15):
        trial = program.model_copy(update={"bricks_per_course": count})
        if not any(i.check == "dimensions" for i in verify(compile_program(trial, config), config).issues):
            fitting.append(count)
    return {"max_bricks_per_course_dimensions": max(fitting, default=None),
            "max_gap_ratio_zero_rotation": maximum(2, lambda gap: candidate(gap, 0)),
            "max_rotation_deg_current_gap": maximum(config["geometry"]["yaw_limit_deg"], lambda angle: candidate(program.gap_ratio, angle)),
            "scope": "All geometric checks, unchanged counts/bond/opening mask/curve. Sampled feasible intervals then binary refinement; not a global optimum or physics approval. Null means no sampled feasible value: change edge supports, openings or dimensions first."}
