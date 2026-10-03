import json
from masonmind.config import load_config
from masonmind.orchestrator import Orchestrator


def test_b0_has_no_design_llm_calls(tmp_path):
    cfg = load_config()
    cfg["variant"] = "B0"
    owner = Orchestrator(cfg, run=tmp_path)
    design, sequence = owner.run_phase_a("ignored by fixed baseline")
    assert len(sequence.order) == 12
    assert not (tmp_path / "llm_log.jsonl").exists()
    assert not owner.memory.enabled


def test_b1_uses_one_role_for_design(tmp_path):
    cfg = load_config()
    cfg["variant"] = "B1"
    owner = Orchestrator(cfg, {**load_config("llm"), "cache": False}, tmp_path)
    owner.run_phase_a("wall")
    calls = [json.loads(line) for line in (tmp_path / "llm_log.jsonl").read_text().splitlines()]
    assert {call["agent"] for call in calls} == {"SingleAgent"}
    assert not owner.memory.enabled
