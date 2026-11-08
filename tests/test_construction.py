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


def test_retry_preserves_virtual_segment_start(tmp_path):
    """Preplanned Cartesian segments must not jump back to measured encoders."""
    from masonmind.planning.rrt_connect import PlanningError
    session = make_session(tmp_path)
    start, goal = np.ones(7), np.ones(7) * 2
    attempts = []

    def plan(begin, end):
        attempts.append(begin.copy())
        if len(attempts) == 1:
            raise PlanningError("Temporary obstruction")
        return [begin, end]

    session.arm = SimpleNamespace(phase="place", state=lambda: np.zeros(7))
    session.controller = SimpleNamespace(plan=plan)
    session.supervisor = lambda **evidence: SupervisorDecision(action="WAIT", wait_seconds=1, reasoning="clear obstruction")
    session.apply_decision = lambda decision: None
    result = session.supervised_plan(start, goal)
    assert len(attempts) == 2
    assert all(np.array_equal(value, start) for value in attempts)
    assert np.array_equal(result[0], start)


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


def test_course_acceptance_cannot_override_failed_scan(tmp_path):
    session = make_session(tmp_path)
    session.variant = "B3"
    session.scan = lambda bricks: SimpleNamespace(deviations=[SimpleNamespace(within_tolerance=False)], broken_ids=[])
    session.supervisor = lambda **evidence: SupervisorDecision(action="ACCEPT", reasoning="incorrect approval")
    with pytest.raises(RuntimeError, match="cannot override"):
        session.after_course(0)


def test_invalid_accept_is_repaired_before_execution(tmp_path):
    import json
    from masonmind.schemas import BrickDeviation
    session = make_session(tmp_path)
    session.controller = None
    session.done = ["c00_b00"]

    class RetryClient:
        calls = 0

        def complete(self, system, messages, images=None, json_schema=None):
            self.calls += 1
            return json.dumps({"action": "ACCEPT" if self.calls == 1 else "REPOSITION",
                "brick_ids": [] if self.calls == 1 else ["c00_b00"], "reasoning": "review"})

    client = RetryClient()
    session.owner.client = client
    deviation = BrickDeviation(brick_id="c00_b00", dx_mm=0, dy_mm=0, dz_mm=0,
                               dyaw_deg=3, missing=False, within_tolerance=False)
    decision = session.supervisor(course=0, deviations=[deviation], broken_ids=[])
    assert decision.action == "REPOSITION"
    assert client.calls == 2
    assert session.interventions.get("ACCEPT", 0) == 0
    entries = [json.loads(line) for line in (tmp_path / "llm_log.jsonl").read_text().splitlines()]
    assert "Action must be one of" in entries[0]["validation_error"]


def test_supervisor_reviews_even_a_conforming_course(tmp_path):
    session = make_session(tmp_path)
    session.variant = "B3"
    session.scan = lambda bricks: SimpleNamespace(deviations=[SimpleNamespace(within_tolerance=True)], broken_ids=[])
    reviewed = []
    def supervise(**evidence):
        reviewed.append(evidence)
        return SupervisorDecision(action="ACCEPT", reasoning="measured conformance")
    session.supervisor = supervise
    session.review_course = lambda course, scan: None
    session.after_course(0)
    assert len(reviewed) == 1
    assert reviewed[0]["course"] == 0


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
