"""Measurements derived from actual run artifacts, never synthetic success rows."""
import hashlib
import json
from pathlib import Path
import numpy as np


def enrich_metrics(run: Path, llm_config: dict, events: list, replan_times: list[float]) -> dict:
    """Aggregate costs, scans, repairs and sequencing from persisted evidence."""
    calls = [json.loads(line) for line in (run / "llm_log.jsonl").read_text(encoding="utf-8").splitlines()] if (run / "llm_log.jsonl").exists() else []
    uncached = [call for call in calls if not call["cached"]]
    input_tokens = sum((call.get("usage", {}).get("input_tokens") or call.get("usage", {}).get("prompt_tokens") or 0) for call in uncached)
    output_tokens = sum((call.get("usage", {}).get("output_tokens") or call.get("usage", {}).get("completion_tokens") or 0) for call in uncached)
    cost = None
    if llm_config["provider"] == "mock":
        cost = 0.0
    elif llm_config.get("input_cost_per_million") is not None and llm_config.get("output_cost_per_million") is not None:
        cost = (input_tokens * llm_config["input_cost_per_million"] + output_tokens * llm_config["output_cost_per_million"]) / 1_000_000
    scans = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((run / "scans").glob("*/accuracy.json"))]
    records = [record for scan in scans for record in scan["records"]]
    first = json.loads((run / "verification_v1.json").read_text(encoding="utf-8"))
    stability_failures = 0
    for path in run.glob("sequence_v*.json"):
        stability_failures += sum(issue["check"] == "intermediate_stability" for issue in json.loads(path.read_text())["report"]["issues"])
    designs = sorted(run.glob("design_v*.json"), key=lambda path: int(path.stem.split("v")[-1]))
    design = json.loads(designs[-1].read_text())
    signature = hashlib.sha256(json.dumps([(b["x"], b["y"], b["z"], b["yaw_deg"], b["is_opening"]) for b in design["bricks"]]).encode()).hexdigest()
    successes, failures = sum(e.kind == "replan_ok" for e in events), sum(e.kind == "replan_failed" for e in events)
    return {"llm_calls": len(uncached), "agent_requests": len(calls), "cache_hits": len(calls) - len(uncached),
        "llm_input_tokens": input_tokens, "llm_output_tokens": output_tokens, "estimated_cost_usd": cost,
        "first_attempt_verified": first["passed"], "design_signature": signature,
        "intermediate_stability_failures": stability_failures,
        "replan_success_rate": successes / (successes + failures) if successes + failures else None,
        "mean_replan_time_s": float(np.mean(replan_times)) if replan_times else None,
        "scan_translation_error_mm": float(np.mean([r["translation_error_mm"] for r in records])) if records else None,
        "scan_yaw_error_deg": float(np.mean([r["yaw_error_deg"] for r in records])) if records else None}
