"""Deterministic offline agent fixtures, including a repairable design."""
import json


class MockClient:
    """Choose canned decisions from explicit input evidence, not call count."""

    def complete(self, system: str, messages: list[dict], images: list[bytes] | None = None,
                 json_schema: dict | None = None) -> str:
        """Produce a valid response for each known role."""
        agent = system.splitlines()[0].removeprefix("Agent: ").strip()
        data = json.loads(messages[0]["content"])
        program = {"n_courses": data.get("n_courses", 3), "bricks_per_course": data.get("bricks_per_course", 4),
                   "bond": "stack", "rotation_rules": [], "opening_rules": [],
                   "seed": data.get("seed", 7), "rationale": "Offline fixture: stable stack bond after deterministic repair."}
        if agent == "Architect":
            if not data.get("feedback"):
                program["opening_rules"] = [{"region": {"course_start": 0, "course_end": 0},
                                             "kind": "stripe", "size": 0.5}]
            result = {"program": program}
        elif agent == "Clarifier":
            result = {"questions": [], "brief": data.get("user_text", "A small masonry wall")}
        elif agent == "SequenceStrategist":
            result = {"strategy": "left_to_right", "rationale": "Build supported courses from left to right."}
        elif agent in ("StructuralEngineer", "RobotOperator"):
            passed = data.get("passed", False)
            result = {"approved": passed, "requested_changes": [] if passed else ["Resolve deterministic report failures."],
                      "reasoning": "Decision follows supplied deterministic feasibility evidence."}
        elif agent == "Coordinator":
            result = {"approved": all(r["approved"] for r in data["reviews"]), "feedback": []}
        elif agent == "SiteSupervisor":
            missing = [d["brick_id"] for d in data.get("deviations", []) if d["missing"]]
            wrong = [d["brick_id"] for d in data.get("deviations", []) if not d["within_tolerance"]]
            result = {"action": "REPLACE" if missing else "REPOSITION" if wrong else "ACCEPT",
                      "brick_ids": missing or wrong, "reasoning": "Act on measured deviations."}
            if data.get("blocked"):
                result.update(action="WAIT", wait_seconds=2.0)
            if data.get("shortage") or data.get("user_change"):
                result.update(action="ASK_USER", question_for_user="Supply is exhausted or the brief changed. Provide a revised brief or replenish material.")
        elif agent == "VLMCritic":
            result = {"intent_match_score": 7.0, "issues": ["Mock score is a fixture, not a visual assessment."]}
        elif agent == "MemoryKeeper":
            result = {"rules": [{"text": "Inspect each completed course and correct measured placement errors before adding load.",
                                  "run_id": data.get("run_id", "offline"), "course": data.get("course", 0),
                                  "brick_ids": data.get("brick_ids", []), "confidence": 0.6}]}
        else:
            raise ValueError(f"Unknown mock agent: {agent}")
        return json.dumps(result)
