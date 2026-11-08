from masonmind.live import stream_work
from masonmind.ui import prepare_design
from masonmind.orchestrator import Orchestrator
from masonmind.config import load_config
import pytest
from masonmind.interaction import ClarificationRequired, answered_brief


def test_live_events_preserve_order_and_report_errors():
    def job(publish):
        publish({"kind": "verification", "passed": False})
        publish({"kind": "verification", "passed": True})
        return "approved"

    events = list(stream_work(job))
    assert [e["kind"] for e in events] == ["verification", "verification", "result"]
    assert events[-1]["value"] == "approved"

    def failure(publish):
        raise RuntimeError("Model unavailable")

    assert list(stream_work(failure))[-1] == {"kind": "error", "message": "RuntimeError: Model unavailable"}


def test_real_provider_never_silently_falls_back(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="No mock fallback"):
        prepare_design("A wave wall", "B3", 7, "openai", "configured-model")


def test_clarification_is_recoverable_and_preserves_user_answer():
    def job(publish):
        raise ClarificationRequired(["Which bond?", "Which bond?"])
    assert list(stream_work(job)) == [{"kind": "input_required", "questions": ["Which bond?"]}]
    assert answered_brief("Original wall", "Stack") == "Original wall\n\nUser clarification:\nStack"
    with pytest.raises(ValueError, match="answer"):
        answered_brief("Original wall", " ")


def test_worker_reports_waiting_then_finishes():
    from threading import Event
    release = Event()
    events = stream_work(lambda publish: release.wait(5))
    try:
        assert next(events)["kind"] == "heartbeat"
        release.set()
        assert next(events)["kind"] == "result"
    finally:
        release.set()
        events.close()


def test_design_emits_repair_and_distinct_role_decisions(tmp_path):
    events = []
    cfg = load_config()
    cfg["memory"]["enabled"] = False
    owner = Orchestrator(cfg, {**load_config("llm"), "cache": False}, tmp_path, observer=events.append)
    owner.run_phase_a("An offline integration test")
    roles = {e["agent"] for e in events if e["kind"] == "agent_decision"}
    assert {"Architect", "StructuralEngineer", "RobotOperator", "Coordinator"} <= roles
    assert [e["passed"] for e in events if e["kind"] == "verification"] == [False, True]
    assert events[-1]["kind"] == "design_approved"
