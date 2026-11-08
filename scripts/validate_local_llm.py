"""Explicit local-model acceptance run; never part of offline pytest."""
from masonmind.ui import prepare_design, build_design
from masonmind.io import save_json
from pathlib import Path


def main() -> None:
    """Check requested design, contrasting brief, and a small real-agent build."""
    def progress(event: dict) -> None:
        if event["kind"] != "frame":
            print(event["kind"], event.get("agent", event.get("brick", "")), flush=True)

    wave = "Build an 8-course wall with 10 bricks per course. Rotate the bricks in a wave pattern, and shift the wave slightly from bottom to top."
    state, preview, review, _, _ = prepare_design(wave, "B3", 7, "ollama", "qwen3.5:4b", progress)
    program = state["design"].program
    assert program.n_courses == 8 and program.bricks_per_course == 10
    assert len(state["sequence"].order) == 80
    assert not program.opening_rules
    assert any(r.kind == "wave" and r.amplitude_deg > 0 and r.phase_shift_per_course != 0 for r in program.rotation_rules)
    print("WAVE_ACCEPTED", review["run"], flush=True)
    small, _, small_review, _, _ = prepare_design(
        "Build exactly 3 courses with exactly 4 bricks per course. A straight, filled stack-bond wall, no rotations, no openings.",
        "B3", 7, "ollama", "qwen3.5:4b", progress)
    other = small["design"].program
    assert other.n_courses == 3 and other.bricks_per_course == 4
    assert not other.rotation_rules and not other.opening_rules
    _, report, _, _, status = build_design(small, "clean", False, progress)
    print(status, flush=True)
    save_json(Path("runs/local_llm_acceptance.json"), {"wave_run": review["run"], "wave_preview": preview,
              "small_run": small_review["run"], "small_report": report})
    assert report["completed"], report["limitations"]


if __name__ == "__main__":
    main()
