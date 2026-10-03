from masonmind.config import load_config
from masonmind.schemas import DesignProgram, SequenceStrategy, Brick, Design
from masonmind.design.compiler import compile_program
from masonmind.design.sequencer import plan_sequence, dependencies
from masonmind.sim.stability import drop_test


def test_sequence_two_strategies_and_precedence():
    design = compile_program(DesignProgram(n_courses=3, bricks_per_course=4, bond="stack", seed=1, rationale="test"))
    graph = dependencies(design)
    orders = []
    for name in ("left_to_right", "right_to_left"):
        sequence = plan_sequence(design, SequenceStrategy(strategy=name, rationale="test"))
        assert sequence.report.passed, sequence.report
        assert len(sequence.order) == 12
        for brick, parents in graph.items():
            assert all(sequence.order.index(parent) < sequence.order.index(brick) for parent in parents)
        orders.append(sequence.order)
    assert orders[0] != orders[1]


def test_drop_test_detects_unsupported_fall():
    brick = Brick(id="floating", course=2, index=0, x=.48, y=0, z=.12, yaw_deg=0)
    result = drop_test([brick])
    assert not result.passed
    assert result.unstable_ids == ["floating"]


def test_gripper_clearance_is_directional():
    cfg = load_config()
    a = Brick(id="a", course=0, index=0, x=.45, y=0, z=.048, yaw_deg=45)
    b = Brick(id="b", course=0, index=1, x=.475, y=-.035, z=.048, yaw_deg=0)
    design = Design(program=DesignProgram(n_courses=3, bricks_per_course=4, bond="stack", seed=1, rationale="directional clearance"), bricks=[a, b])
    left = plan_sequence(design, SequenceStrategy(strategy="left_to_right", rationale="test"), cfg)
    right = plan_sequence(design, SequenceStrategy(strategy="right_to_left", rationale="test"), cfg)
    assert left.report.passed
    assert not right.report.passed
    assert any(i.check == "gripper_clearance" for i in right.report.issues)
