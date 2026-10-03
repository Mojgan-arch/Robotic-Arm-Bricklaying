import json
from masonmind.memory import MemoryStore
from masonmind.schemas import MemoryRule
from masonmind.orchestrator import Orchestrator
from masonmind.config import load_config


def test_memory_confirmation_retrieval_and_disable(tmp_path):
    config = {"enabled": True, "path": str(tmp_path / "rules.jsonl"), "max_rules": 2}
    store = MemoryStore(config)
    rule = MemoryRule(text="Check support before increasing wall ventilation", run_id="a", course=0, confidence=.7)
    store.add(rule)
    store.add(rule.model_copy(update={"run_id": "b"}))
    assert store.retrieve("wall support")[0].times_confirmed == 2
    assert len((tmp_path / "rules.jsonl").read_text().splitlines()) == 2
    disabled = MemoryStore({**config, "enabled": False})
    disabled.add(rule)
    assert disabled.retrieve("wall") == []
    assert len((tmp_path / "rules.jsonl").read_text().splitlines()) == 2


def test_rules_reach_future_architect_prompt(tmp_path):
    cfg = load_config()
    cfg["memory"]["path"] = str(tmp_path / "memory.jsonl")
    rule = MemoryRule(text="Inspect each completed course for placement error", run_id="earlier", course=1, confidence=.7)
    MemoryStore(cfg["memory"]).add(rule)
    llm = {**load_config("llm"), "cache": False}
    owner = Orchestrator(cfg, llm, tmp_path / "run")
    owner.run_phase_a("wall placement")
    log = [json.loads(line) for line in (owner.run / "llm_log.jsonl").read_text().splitlines()]
    architect = next(item for item in log if item["agent"] == "Architect")
    assert rule.text in architect["messages"][0]["content"]
