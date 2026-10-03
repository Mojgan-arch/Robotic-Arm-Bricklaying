from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from masonmind.config import load_config
from masonmind.design.compiler import compile_program
from masonmind.design.sequencer import plan_sequence
from masonmind.orchestrator import Orchestrator, BuildSession
from masonmind.schemas import DesignProgram, SequenceStrategy, SupervisorDecision
from masonmind.planning.executor import speed_scale


def make_session(tmp_path):
    owner = Orchestrator(run=tmp_path, llm_config={**load_config("llm"), "cache": False})
    design = compile_program(DesignProgram(n_courses=3, bricks_per_course=4, bond="stack", seed=7, rationale="test"))
    sequence = plan_sequence(design, SequenceStrategy(strategy="left_to_right", rationale="test"))
    return BuildSession(owner, design, sequence, "clean", "B0", False)


def test_speed_zones():
    assert speed_scale(.2, .03, .1) == 1
    assert speed_scale(.03, .03, .1) == 0
    assert speed_scale(.065, .03, .1) == pytest.approx(.5)
    with pytest.raises(ValueError):
        speed_scale(.1, .2, .1)


def test_replan_remaining_preserves_built_course(tmp_path):
    session = make_session(tmp_path)
    session.done = session.remaining[:4]
    session.remaining = session.remaining[4:]
    original = [b.model_copy(deep=True) for b in session.design.bricks[:4]]
    replacement = session.design.program.model_copy(update={"n_courses": 4})
    session.apply_decision(SupervisorDecision(action="REPLAN_REMAINING", new_program_for_remaining=replacement, reasoning="extend"))
    assert session.design.bricks[:4] == original
    assert len(session.remaining) == 12
    session.apply_decision(SupervisorDecision(action="RESEQUENCE", new_strategy=SequenceStrategy(strategy="right_to_left", rationale="alternate"), reasoning="blocked"))
    assert session.remaining[0] == "c01_b03"


def test_wait_and_scripted_stock_request(tmp_path):
    session = make_session(tmp_path)
    steps = []
    session.world = SimpleNamespace(step=lambda count: steps.append(count))
    session.arm = SimpleNamespace(state=lambda: np.zeros(7), command=lambda value: None)
    session.apply_decision(SupervisorDecision(action="WAIT", wait_seconds=2, reasoning="obstacle"))
    assert steps == [480]
    session.stock = 0
    session.apply_decision(SupervisorDecision(action="ASK_USER", question_for_user="Replenish?", reasoning="empty"))
    assert session.stock > 0


def test_loaded_brick_cannot_be_extracted(tmp_path):
    session = make_session(tmp_path)
    session.done = session.remaining[:8]
    session.latest_scan = SimpleNamespace(poses={})
    with pytest.raises(RuntimeError, match="loaded brick"):
        session.apply_decision(SupervisorDecision(action="REPOSITION", brick_ids=["c00_b00"], reasoning="unsafe"))


def test_phase_b_rejects_incomplete_order(tmp_path):
    session = make_session(tmp_path)
    session.sequence.order = session.sequence.order[:-1]
    with pytest.raises(ValueError, match="exactly once"):
        session.owner.run_phase_b(session.design, session.sequence, video=False)


@pytest.mark.parametrize("action", ["REPOSITION", "REPLACE"])
def test_correction_actions_physically_move_brick(tmp_path, action):
    import pybullet as p
    from masonmind.sim.env import World
    from masonmind.sim.robot import Panda
    from masonmind.planning.proxies import ProxyScene
    from masonmind.planning.collision import CollisionChecker
    session = make_session(tmp_path)
    brick = session.design.bricks[0]
    source = [brick.x + .004, brick.y, brick.z]
    with World(session.config) as world:
        session.world, session.arm = world, Panda(world)
        session.proxies = ProxyScene(world)
        session.checker = CollisionChecker(session.arm, [world.table])
        original = world.spawn_brick(brick.id, source)
        session.done = [brick.id]
        session.latest_scan = SimpleNamespace(poses={brick.id: (source, 0)})
        session.apply_decision(SupervisorDecision(action=action, brick_ids=[brick.id], reasoning="test measured error"))
        position, _ = p.getBasePositionAndOrientation(world.bricks[brick.id], physicsClientId=world.cid)
        assert np.linalg.norm(np.asarray(position) - [brick.x, brick.y, brick.z]) < .003
        if action == "REPLACE":
            assert world.bricks[brick.id] != original
            assert any(key.startswith("discarded_") for key in world.bricks)
