from copy import deepcopy
import time
import pytest
from masonmind.config import load_config
from masonmind.schemas import DesignProgram, RotationRule, Region, OpeningRule
from masonmind.design.compiler import compile_program
from masonmind.design.verifier import verify


def program(**updates):
    values = dict(n_courses=3, bricks_per_course=4, bond="stack", seed=7, rationale="test")
    values.update(updates)
    return DesignProgram(**values)


def test_deterministic_compiler_and_rule_order():
    region = Region(course_start=0, course_end=2)
    rules = [RotationRule(region=region, kind="constant", amplitude_deg=40),
             RotationRule(region=region, kind="constant", amplitude_deg=30),
             RotationRule(region=region, kind="constant", amplitude_deg=8, combine="override")]
    design = compile_program(program(rotation_rules=rules))
    assert design == compile_program(program(rotation_rules=rules))
    assert {b.yaw_deg for b in design.bricks} == {8}
    assert {b.yaw_deg for b in compile_program(program(rotation_rules=rules[:2])).bricks} == {45}


@pytest.mark.parametrize("kind", ["constant", "wave", "alternate"])
def test_three_visually_distinct_valid_walls(kind):
    cfg = load_config()
    cfg["geometry"]["pitch"] = 0.062
    rules = [RotationRule(region=Region(course_start=0, course_end=2), kind=kind, amplitude_deg=20)]
    design = compile_program(program(rotation_rules=rules), cfg)
    report = verify(design, cfg)
    assert report.passed, report


def test_running_bond_and_support():
    design = compile_program(program(bond="running"))
    assert verify(design).passed
    assert design.bricks[4].x - design.bricks[0].x == pytest.approx(0.027)


@pytest.mark.parametrize("check", ["overlap", "support", "com", "dimensions"])
def test_hand_made_failures(check):
    design = compile_program(program())
    if check == "overlap":
        design.bricks[1].x = design.bricks[0].x
    elif check in ("support", "com"):
        design.bricks[4].y += 0.04
    else:
        design.bricks[0].x = 2
    report = verify(design)
    assert not report.passed
    assert any(i.check == check and i.message and i.brick_ids for i in report.issues)


def test_com_failure_even_when_support_ratio_passes():
    design = compile_program(program())
    # Narrow gap directly under the upper brick's centre, with >50% area supported.
    design.bricks[0].x = design.bricks[4].x - 0.028
    design.bricks[1].x = design.bricks[4].x + 0.028
    report = verify(design)
    assert any(i.check == "com" and i.brick_ids == ["c01_b00"] for i in report.issues)
    assert not any(i.check == "support" and i.brick_ids == ["c01_b00"] for i in report.issues)


def test_verifier_runtime():
    design = compile_program(program(n_courses=15, bricks_per_course=14))
    started = time.perf_counter()
    verify(design)
    assert time.perf_counter() - started < 1


def test_opening_seed_and_shape():
    rule = OpeningRule(region=Region(course_start=0, course_end=2), kind="random", ratio_end=0.5)
    a = compile_program(program(opening_rules=[rule]))
    assert a == compile_program(program(opening_rules=[rule]))
    assert any(b.is_opening for b in a.bricks)
