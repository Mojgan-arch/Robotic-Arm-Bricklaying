"""Optional local Gradio interface for reviewable design and recorded construction."""
import json
from typing import TYPE_CHECKING
from pathlib import Path
import imageio.v2 as imageio
from PIL import Image
from masonmind.config import load_config
from masonmind.orchestrator import Orchestrator
from masonmind.sim.env import World
from masonmind.sim.camera import Camera

if TYPE_CHECKING:
    import gradio as gr


def conversation(run: Path) -> str:
    """Present role responses without credentials or hidden reasoning."""
    path = run / "llm_log.jsonl"
    if not path.exists():
        return "No agent calls (fixed baseline)."
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return "\n\n".join(f"{entry['agent']} | {entry['latency_s']:.3f}s | cache={entry['cached']}\n{entry['response']}" for entry in entries)


def prepare_design(brief: str, variant: str, seed: int) -> tuple:
    """Create a verified design, render its placement order, and retain review state."""
    if not brief.strip():
        raise ValueError("Describe a wall before generating a design")
    cfg = load_config()
    cfg["seed"], cfg["variant"] = int(seed), variant
    cfg["memory"]["enabled"] = variant == "B3"
    owner = Orchestrator(cfg)
    design, sequence = owner.run_phase_a(brief)
    animation = owner.run / "sequence.gif"
    with World(cfg, robot=False) as world:
        camera = Camera(world)
        by_id = {brick.id: brick for brick in design.bricks}
        with imageio.get_writer(str(animation), mode="I", duration=250, loop=0) as writer:
            writer.append_data(camera.capture(unproject=False).rgb)
            for name in sequence.order:
                brick = by_id[name]
                world.spawn_brick(name, [brick.x, brick.y, brick.z], brick.yaw_deg)
                writer.append_data(camera.capture(unproject=False).rgb)
            for _ in range(4):
                writer.append_data(camera.capture(unproject=False).rgb)
        Image.fromarray(camera.capture(unproject=False).rgb).save(owner.run / "preview.png")
    state = {"owner": owner, "design": design, "sequence": sequence, "variant": variant}
    review = {"program": design.program.model_dump(), "sequence": sequence.model_dump(),
              "repair_rounds": owner.repair_rounds, "run": str(owner.run.resolve())}
    return state, str(animation.resolve()), review, conversation(owner.run), f"Verified {len(sequence.order)} bricks. Review the design, then start construction."


def build_design(state: dict | None, scenario: str, record: bool) -> tuple:
    """Build the reviewed design and return explicit success/failure evidence."""
    if state is None:
        raise ValueError("Generate and review a design first")
    if state.get("built"):
        raise ValueError("This run has already been built. Generate a new design for another run.")
    state["built"] = True
    owner = state["owner"]
    report = owner.run_phase_b(state["design"], state["sequence"], scenario, state["variant"], record)
    scans = sorted((owner.run / "scans").glob("*/deviations.json"))
    rows = []
    if scans:
        rows = [[d["brick_id"], d["dx_mm"], d["dy_mm"], d["dz_mm"], d["dyaw_deg"], d["within_tolerance"], d["missing"]]
                for d in json.loads(scans[-1].read_text(encoding="utf-8"))]
    video = owner.run / "video.mp4"
    status = ("Construction completed." if report.completed else "Construction did not complete; inspect the report and decisions.") + f" Artifacts: {owner.run.resolve()}"
    return str(video.resolve()) if video.exists() else None, report.model_dump(), rows, conversation(owner.run), status


def create_app() -> "gr.Blocks":
    """Build the optional UI without launching a server or downloading resources."""
    try:
        import gradio as gr
    except ImportError as error:
        raise RuntimeError('Install the UI extra first: pip install -e ".[ui,scan,ifc]"') from error
    def prepare_callback(brief: str, variant: str, seed: int) -> tuple:
        try:
            return prepare_design(brief, variant, seed)
        except (RuntimeError, ValueError) as error:
            raise gr.Error(str(error)) from error

    def build_callback(state: dict | None, scenario: str, record: bool) -> tuple:
        try:
            return build_design(state, scenario, record)
        except (RuntimeError, ValueError) as error:
            raise gr.Error(str(error)) from error

    with gr.Blocks(title="MasonMind", theme=gr.themes.Soft(), analytics_enabled=False) as app:
        gr.Markdown("# MasonMind\n### Describe. Verify. Assemble.\nDeterministic robotics with task-level agents. The default **MockClient is an offline fixture**, not a language-understanding model.")
        state = gr.State(None)
        with gr.Row():
            with gr.Column(scale=1):
                brief = gr.Textbox(label="Wall brief", value="A stable three-course wall", lines=4)
                variant = gr.Dropdown(["B0", "B1", "B2", "B3"], value="B3", label="System variant")
                seed = gr.Number(value=7, precision=0, minimum=0, label="Random seed")
                prepare = gr.Button("1. Generate and verify", variant="primary")
                scenario = gr.Dropdown(["clean", "noisy_placement", "drops", "broken_bricks", "material_shortage", "user_change", "moving_obstacle", "blocking_obstacle", "everything"], value="clean", label="Construction scenario")
                record = gr.Checkbox(value=True, label="Record MP4 (slower)")
                build = gr.Button("2. Build reviewed design", variant="primary")
                status = gr.Markdown("Generate a design to begin. If clarification is requested, add the answers to your brief and regenerate.")
            with gr.Column(scale=2):
                animation = gr.Image(label="Verified sequence preview", type="filepath")
                video = gr.Video(label="Recorded build (available after completion)")
        with gr.Tab("Verification and report"):
            review = gr.JSON(label="Design and sequence")
            report = gr.JSON(label="Build report")
            deviations = gr.Dataframe(headers=["Brick", "dx mm", "dy mm", "dz mm", "yaw deg", "Within tolerance", "Unobserved"], label="Final scan")
        with gr.Tab("Agent conversation"):
            log = gr.Textbox(lines=18, label="Validated responses")
        prepare.click(prepare_callback, [brief, variant, seed], [state, animation, review, log, status])
        build.click(build_callback, [state, scenario, record], [video, report, deviations, log, status], concurrency_limit=1)
    return app
