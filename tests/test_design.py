import time
import pytest
import math
from masonmind.config import load_config
from masonmind.schemas import DesignProgram, RotationRule, Region, OpeningRule
from masonmind.design.compiler import compile_program
from masonmind.design.verifier import verify


def program(**updates):
    values = dict(n_courses=3, bricks_per_course=4, bond="stack", seed=7, rationale="test")
    values.update(updates)
    return DesignProgram(**values)


def test_workcell_rotation_preserves_local_design_and_length_guard():
    cfg = load_config()
    source = compile_program(program(), cfg)
    cfg["geometry"]["wall_yaw_deg"] = 90
    turned = compile_program(program(), cfg)
    ox, oy, _ = cfg["geometry"]["origin"]
    for a, b in zip(source.bricks, turned.bricks):
        assert b.x == pytest.approx(ox)
        assert b.y == pytest.approx(oy + a.x - ox)
        assert math.isclose(b.yaw_deg, a.yaw_deg + 90)
    assert verify(turned, cfg).passed
    cfg["geometry"]["max_length"] = .1
    assert any("Wall length" in issue.message for issue in verify(turned, cfg).issues)


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
    design = compile_program(program(bond="running", end_treatment="none"))
    cfg = load_config()
    cfg["geometry"]["com_margin_m"] = 0  # legacy end bricks balance at the boundary
    assert verify(design, cfg).passed
    assert any(i.check == "com" for i in verify(design).issues)
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


def test_bridge_com_over_gap_passes():
    design = compile_program(program())
    # Narrow gap directly under the upper brick's centre, with >50% area supported.
    design.bricks[0].x = design.bricks[4].x - 0.028
    design.bricks[1].x = design.bricks[4].x + 0.028
    report = verify(design)
    assert not any(i.check == "com" and i.brick_ids == ["c01_b00"] for i in report.issues)
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


@pytest.mark.parametrize("sweep", [-30, 30])
def test_arc_has_exact_radius_tangent_and_layer_alignment(sweep):
    cfg = load_config()
    cfg["geometry"]["wall_yaw_deg"] = 90
    design = compile_program(program(arc_angle_deg=sweep), cfg)
    radius = 3 * cfg["geometry"]["pitch"] / math.radians(sweep)
    ox, oy, _ = cfg["geometry"]["origin"]
    for brick in design.bricks:
        assert math.hypot(brick.x - (ox - radius), brick.y - oy) == pytest.approx(abs(radius))
        assert brick.yaw_deg == pytest.approx(90 + (brick.index / 3 - .5) * sweep)
    assert verify(design, cfg).passed


def test_length_gradient_endpoints_and_regional_reset():
    rule = RotationRule(region=Region(course_start=0, course_end=2),
                        kind="length_gradient", start_deg=0, amplitude_deg=35)
    design = compile_program(program(rotation_rules=[rule], arc_angle_deg=20))
    for course in range(3):
        yaws = [b.yaw_deg - (b.index / 3 - .5) * 20 for b in design.bricks if b.course == course]
        assert yaws == pytest.approx([0, 35 / 3, 70 / 3, 35])


def test_perforation_is_exact_and_unsupported_request_is_rejected():
    rule = OpeningRule(region=Region(course_start=0, course_end=11), kind="hit_and_miss")
    p = program(n_courses=12, bricks_per_course=14, bond="running", end_treatment="none", arc_angle_deg=20,
                required_supports=2, opening_rules=[rule])
    cfg = load_config()
    cfg["geometry"]["wall_yaw_deg"] = 90
    design = compile_program(p, cfg)
    assert sum(not b.is_opening for b in design.bricks) == 84
    assert all(b.is_opening == ((b.course + b.index) % 2 == 1) for b in design.bricks)
    assert [b.is_opening for b in compile_program(p.model_copy(update={"seed": 42}), cfg).bricks] == [b.is_opening for b in design.bricks]
    issues = verify(design, cfg).issues
    assert any(i.check == "support" and "requires 2" in i.message for i in issues)
    assert any(i.check == "dimensions" for i in issues)


def test_curve_does_not_hide_overlength_wall():
    cfg = load_config()
    cfg["geometry"]["max_length"] = .22
    design = compile_program(program(arc_angle_deg=90), cfg)
    assert any(i.check == "dimensions" and "Wall length" in i.message for i in verify(design, cfg).issues)
