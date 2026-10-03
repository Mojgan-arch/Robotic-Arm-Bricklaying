import json
import pytest
from masonmind.config import load_config
from masonmind.agents import Clarifier
from masonmind.llm.mock import MockClient
from masonmind.orchestrator import Orchestrator


def test_validation_retries_and_cache(tmp_path):
    class RepairClient(MockClient):
        calls = 0
        def complete(self, *args, **kwargs):
            self.calls += 1
            return "{}" if self.calls == 1 else super().complete(*args, **kwargs)
    cfg = {**load_config("llm"), "cache_dir": str(tmp_path / "cache")}
    client = RepairClient()
    agent = Clarifier(client, cfg, tmp_path)
    assert agent.decide({"user_text": "wall"}).brief == "wall"
    assert client.calls == 2
    log = [json.loads(line) for line in (tmp_path / "llm_log.jsonl").read_text().splitlines()]
    assert "validation_error" in log[0]
    # Separate unbroken request has its own cache key; repeated request hits it.
    agent.decide({"user_text": "another wall"})
    before = client.calls
    agent.decide({"user_text": "another wall"})
    assert client.calls == before


def test_invalid_agent_raises_after_three_retries(tmp_path):
    class Invalid:
        calls = 0
        def complete(self, *args, **kwargs):
            self.calls += 1
            return "invalid"
    client = Invalid()
    cfg = {**load_config("llm"), "cache": False}
    with pytest.raises(RuntimeError, match="after 4 attempts"):
        Clarifier(client, cfg, tmp_path).decide({})
    assert client.calls == 4


def test_phase_a_repair_and_export(tmp_path):
    cfg = {**load_config("llm"), "cache_dir": str(tmp_path / "cache")}
    orchestrator = Orchestrator(llm_config=cfg, run=tmp_path)
    design, sequence = orchestrator.run_phase_a("Three-course wall")
    assert orchestrator.repair_rounds >= 1
    assert design.version >= 2
    assert sequence.report.passed
    assert (tmp_path / "phase_a.json").exists()
    try:
        import ifcopenshell
    except ImportError:
        return
    file = ifcopenshell.open(str(tmp_path / f"design_v{design.version}.ifc"))
    assert len(file.by_type("IfcBuildingElementProxy")) == len(sequence.order)
