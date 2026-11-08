"""Optional local Gradio interface for reviewable design and recorded construction."""
import json
import os
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Literal
from pathlib import Path
from PIL import Image
from masonmind.config import ROOT, load_config
from masonmind.orchestrator import Orchestrator
from masonmind.sim.env import World
from masonmind.sim.camera import Camera
from masonmind.live import stream_work
from masonmind.io import json_value
from masonmind.interaction import answered_brief

if TYPE_CHECKING:
    import gradio as gr


def idle_robot_preview() -> str:
    """Render the configured Panda at home, without calling agents or starting a build."""
    from masonmind.sim.robot import Panda
    cfg = load_config()
    cfg["sim"]["camera_eye"] = cfg["ui"]["idle_camera_eye"]
    cfg["sim"]["camera_target"] = cfg["ui"]["idle_camera_target"]
    target = ROOT / "runs" / "web" / "idle_robot.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    with World(cfg) as world:
        Panda(world)
        world.spawn_brick("ready_stock")
        world.spawn_brick("ready_half_stock", brick_type="half")
        world.step(cfg["sim"]["settle_steps"])
        Image.fromarray(Camera(world, resolution=tuple(cfg["ui"]["live_resolution"])).capture(unproject=False).rgb).save(target)
    return str(target.resolve())


def conversation(run: Path) -> str:
    """Present role responses without credentials or hidden reasoning."""
    path = run / "llm_log.jsonl"
    if not path.exists():
        return "No agent calls (fixed baseline)."
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return "\n\n".join(f"{entry['agent']} | provider={entry.get('provider', 'legacy log')} | {entry['latency_s']:.3f}s | cache={entry['cached']}\n{entry['response']}" for entry in entries)


def prepare_design(brief: str, variant: str, seed: int, provider: str | None = None,
                   model: str | None = None, observer: Callable[[dict], None] | None = None,
                   tested_wall: bool | Literal["wave"] = False) -> tuple:
    """Create a verified design, render its placement order, and retain review state."""
    if not brief.strip() and not tested_wall:
        raise ValueError("Describe a wall before generating a design")
    cfg = load_config()
    cfg["seed"], cfg["variant"] = int(seed), variant
    workspace_path = ROOT / "configs/ui_workspace.yaml"
    if workspace_path.exists():
        workspace = load_config(path=workspace_path)
        cfg["geometry"].update(workspace["geometry"])
        cfg["planning"].update(workspace.get("planning", {}))
        cfg["sim"].update(workspace.get("sim", {}))
    if tested_wall == "wave":
        for section, overrides in load_config("connected_wave_workspace").items():
            cfg[section].update(overrides)
    cfg["memory"]["enabled"] = variant == "B3"
    llm = load_config("llm_local" if provider == "ollama" and (ROOT / "configs/llm_local.yaml").exists() else "llm")
    if provider:
        llm["provider"] = provider
        llm["model"] = "deterministic-v1" if provider == "mock" else (model or "").strip()
    if not llm["model"]:
        raise ValueError("Enter the exact name of an installed local model or your API model.")
    required_key = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(llm["provider"])
    if required_key and not os.environ.get(required_key):
        raise ValueError(f"Real LLM unavailable: set {required_key} locally before starting the server. No mock fallback was used.")
    cfg["memory"]["path"] = str(ROOT / "memory" / f"{llm['provider']}_rules.jsonl")
    owner = Orchestrator(cfg, llm, observer=observer)
    if tested_wall:
        from masonmind.schemas import DesignProgram, SequenceStrategy
        from masonmind.design.compiler import compile_program
        from masonmind.design.verifier import verify
        from masonmind.design.sequencer import plan_sequence
        from masonmind.io import save_json
        wave = tested_wall == "wave"
        filename = "connected_wave_wall.json" if wave else "large_studio_wall.json"
        program = DesignProgram.model_validate_json((ROOT / "configs/examples" / filename).read_text())
        owner.brief = ("100-piece connected wave wall: 8 courses, 12 brick positions per course; running bond with half-brick ends. Sinusoidal yaw +/-20 degrees, "
                       "one wave along each course, phase shifts 0.1 radians per course; gap_ratio 0.12, straight centreline."
                       if wave else "Tested 105-piece straight running-bond wall with uniform one-third gaps and half end bricks.")
        owner.emit("design_stage", message=f"Checking the saved {'100-piece connected wave' if wave else '105-piece straight'} design (not generated from the text box)")
        design = compile_program(program, cfg)
        verification = verify(design, cfg)
        save_json(owner.run / "verification_v1.json", verification)
        if not verification.passed:
            raise RuntimeError("Saved wall fails the current workspace checks; see the run log.")
        owner.emit("design_stage", message="Checking placement order and intermediate physical stability")
        sequence = plan_sequence(design, SequenceStrategy(strategy="left_to_right", rationale="Tested wall order"), cfg)
        if not sequence.report.passed:
            raise RuntimeError("Saved wall fails placement-order checks.")
        for name, value in (("program_v1", program), ("design_v1", design),
                            ("verification_v1", verification), ("sequence_v1", sequence)):
            save_json(owner.run / f"{name}.json", value)
        owner.emit("design_approved", bricks=len(sequence.order), source="saved_tested_design")
    else:
        design, sequence = owner.run_phase_a(brief)
    animation = owner.run / "preview.png"
    with World(cfg, robot=False) as world:
        camera = Camera(world)
        by_id = {brick.id: brick for brick in design.bricks}
        for name in sequence.order:
            brick = by_id[name]
            world.spawn_brick(name, [brick.x, brick.y, brick.z], brick.yaw_deg, brick_type=brick.brick_type)
        Image.fromarray(camera.capture(unproject=False).rgb).save(animation)
    state = {"owner": owner, "design": design, "sequence": sequence, "variant": variant}
    review = {"program": design.program.model_dump(), "sequence": sequence.model_dump(),
              "repair_rounds": owner.repair_rounds, "run": str(owner.run.resolve()),
              "provider": owner.llm_config["provider"], "model": owner.llm_config["model"],
              "retrieved_memory": [rule.model_dump() for rule in owner.memory_rules]}
    mode = "OFFLINE FIXTURE — not generated from your brief" if llm["provider"] == "mock" else f"{llm['provider']} / {llm['model']}"
    if tested_wall:
        name = "connected wave wall — 8 courses, 100 pieces, ±20°" if tested_wall == "wave" else "tested wall"
        mode = f"Loaded {name} — not generated from the text box. Construction supervisor: " + mode
    return state, str(animation.resolve()), review, conversation(owner.run), f"{mode}. Verified {len(sequence.order)} bricks. Review the design, then start construction."


def build_design(state: dict | None, scenario: str, record: bool,
                 observer: Callable[[dict], None] | None = None) -> tuple:
    """Build the reviewed design and return explicit success/failure evidence."""
    if state is None:
        raise ValueError("Generate and review a design first")
    if state.get("built"):
        raise ValueError("This run has already been built. Generate a new design for another run.")
    state["built"] = True
    owner = state["owner"]
    owner.observer = observer
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
    def describe(event: dict) -> str:
        return json.dumps(event, default=json_value, ensure_ascii=False, indent=2)

    def prepare_callback(brief: str, variant: str, seed: int, provider: str, model: str,
                         tested_wall: bool | Literal["wave"] = False) -> Iterator[tuple]:
        history, decisions = [], []
        label = "Starting design review"
        yield (None, gr.update(value=None, visible=True), {}, "", label + "…", gr.update(interactive=False), "",
               gr.update(value="", visible=False), gr.update(value="", visible=False), gr.update(visible=False), gr.update(interactive=False))
        for event in stream_work(lambda publish: prepare_design(brief, variant, seed, provider, model, publish, tested_wall)):
            if event["kind"] == "result":
                prepared, preview, review, log, status = event["value"]
                yield (prepared, gr.update(value=preview, visible=True, label="Current verified design — completed wall preview"), review, log, status, gr.update(interactive=True), "\n\n".join(history),
                       gr.skip(), gr.skip(), gr.skip(), gr.update(interactive=True))
            elif event["kind"] == "input_required":
                questions = "### Please clarify\n\n" + "\n\n".join(f"- {q}" for q in event["questions"])
                yield (None, gr.update(value=None, visible=True), {}, "\n\n".join(decisions),
                       "Waiting for your answer below. The app is ready; no construction has started for this request.",
                       gr.update(interactive=False), "\n\n".join(history), gr.update(value=questions, visible=True),
                       gr.update(value="", visible=True), gr.update(visible=True), gr.update(interactive=True))
            elif event["kind"] == "error":
                yield (None, gr.update(value=None, visible=True), {}, gr.skip(),
                       "Design could not finish. Edit the request and generate again. Details: " + event["message"],
                       gr.update(interactive=False), "\n\n".join(history), gr.skip(), gr.skip(), gr.skip(), gr.update(interactive=True))
            elif event["kind"] == "heartbeat":
                yield (gr.skip(), gr.skip(), gr.skip(), gr.skip(), f"{label} — working ({event['elapsed_s']} s elapsed)…",
                       gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip())
            else:
                history.append(describe(event))
                if event["kind"] == "agent_decision":
                    decisions.append(describe(event))
                label = ("Approved — preparing preview…" if event["kind"] == "design_approved"
                         else event.get("message", event.get("agent", event["kind"])))
                yield (gr.skip(), gr.skip(), gr.skip(), "\n\n".join(decisions), f"Design: {label}", gr.skip(), "\n\n".join(history),
                       gr.skip(), gr.skip(), gr.skip(), gr.skip())

    def build_callback(state: dict | None, scenario: str, record: bool) -> Iterator[tuple]:
        history, decisions, rules = [], [], []
        current_image, current_heatmap, rows, result_report, current_video = gr.skip(), None, [], {}, None
        for event in stream_work(lambda publish: build_design(state, scenario, record, publish)):
            kind = event["kind"]
            status_text = kind
            if kind == "heartbeat":
                yield (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                       f"Construction worker active — waiting for simulation/model ({event['elapsed_s']} s elapsed)…", gr.skip())
                continue
            if kind == "frame":
                current_image = event["image"]
                status_text = f"LIVE SIMULATION | {event['phase']} | {event['placed']}/{event['total']} bricks | simulation {event['simulation_time']:.1f}s"
            elif kind == "result":
                current_video, result_report, rows, log_text, status_text = event["value"]
                decisions = [log_text]
            elif kind == "error":
                status_text = event["message"]
                history.append(status_text)
            else:
                history.append(describe(event))
                if kind == "agent_decision":
                    decisions.append(describe(event))
                elif kind == "scan":
                    current_heatmap = event["heatmap"]
                    rows = [[d.brick_id, d.dx_mm, d.dy_mm, d.dz_mm, d.dyaw_deg, d.within_tolerance, d.missing]
                            for d in event["deviations"]]
                elif kind == "memory_rule":
                    rules.append(event["rule"])
            yield (current_image, current_heatmap, rows, "\n\n".join(decisions), "\n\n".join(history),
                   rules, result_report, status_text, current_video)

    sample = ROOT / "assets" / "masonmind_demo.gif"
    configured = load_config("llm_local" if (ROOT / ".local-llm/ready.json").exists() else "llm")
    css = """
    #control-panel { gap: 12px; }
    #compact-settings { gap: 8px; }
    #compact-settings > div { gap: 10px; }
    #control-panel button.primary { min-height: 34px; height: 34px; padding: 0 12px; border-radius: 5px; }
    #robot-workspace:not(.fullscreen), #wall-preview:not(.fullscreen) { height: 360px; }
    #robot-workspace.fullscreen { background: #091018 !important; }
    #robot-workspace.fullscreen .image-container,
    #robot-workspace.fullscreen .image-frame { width: 100% !important; height: calc(100vh - 55px) !important; }
    #robot-workspace.fullscreen img { object-fit: contain !important; width: 100% !important; height: 100% !important; max-width: 100% !important; max-height: 100% !important; }
    """
    saved_preview = None
    for phase in sorted((ROOT / "runs").glob("*/phase_a.json"), reverse=True):
        preview = phase.parent / "preview.png"
        if preview.exists():
            try:
                if json.loads(phase.read_text(encoding="utf-8")).get("approved"):
                    saved_preview = str(preview.resolve())
                    from masonmind.schemas import Design
                    versions = sorted(phase.parent.glob("design_v*.json"), key=lambda p: int(p.stem.split("_v")[-1]))
                    if versions:
                        design = Design.model_validate_json(versions[-1].read_text(encoding="utf-8"))
                        cfg = json.loads((phase.parent / "config.json").read_text(encoding="utf-8"))["physics"]
                        restored = ROOT / "runs/web/restored_wall_preview.png"
                        with World(cfg, robot=False) as world:
                            for b in design.bricks:
                                if not b.is_opening:
                                    world.spawn_brick(b.id, [b.x, b.y, b.z], b.yaw_deg, brick_type=b.brick_type)
                            Image.fromarray(Camera(world).capture(unproject=False).rgb).save(restored)
                        saved_preview = str(restored.resolve())
                    break
            except (OSError, ValueError):
                continue
    with gr.Blocks(title="MasonMind", theme=gr.themes.Soft(), analytics_enabled=False, css=css) as app:
        gr.Markdown("# MasonMind — closed-loop masonry laboratory\nDescribe → Architect → Verify / repair → Engineer & Operator → Build → Scan → Supervisor → Memory\n**Real multi-agent operation requires a connected model. Offline test mode uses canned responses and does not interpret your brief.**")
        state = gr.State(None)
        with gr.Row():
            with gr.Column(scale=1, min_width=500, elem_id="control-panel"):
                with gr.Row(equal_height=True, elem_id="compact-settings"):
                    with gr.Column(scale=2, min_width=180):
                        provider = gr.Dropdown([("Offline fixture — not a real LLM", "mock"), ("Ollama — local LLM", "ollama"),
                                        ("OpenAI — API", "openai"), ("Anthropic — API", "anthropic")],
                                       value=configured["provider"], label="Decision provider")
                        model = gr.Textbox(label="Exact model name (real providers)", value="" if configured["provider"] == "mock" else configured["model"])
                    with gr.Column(scale=1, min_width=100):
                        variant = gr.Dropdown(["B0", "B1", "B2", "B3"], value="B3", label="System variant")
                        seed = gr.Number(value=7, precision=0, minimum=0, label="Random seed")
                brief = gr.Textbox(label="Describe the parametric wall", value="Build a connected wave wall: 8 courses, 12 brick positions per course, running bond with half-brick ends on shifted courses (100 pieces). Sinusoidal brick rotation +/-20 degrees, one wave per course, phase shifts 0.1 radians per course. Narrow gap_ratio 0.12, no omitted bricks, straight centreline.", lines=4)
                prepare = gr.Button("1. Generate and verify", variant="primary")
                with gr.Group():
                    scenario = gr.Dropdown(["clean", "noisy_placement", "drops", "broken_bricks", "material_shortage", "user_change", "moving_obstacle", "blocking_obstacle", "everything"], value="clean", label="Construction scenario")
                    record = gr.Checkbox(value=False, label="Also record full MP4 (slow; live view works without it)")
                build = gr.Button("2. Build reviewed design", variant="primary", interactive=False)
                status = gr.Markdown("**Robot ready — construction has not started.** First select **1. Generate and verify**, review the design, then **2. Build reviewed design**. Offline mode tests the machinery; a connected model is needed to interpret your brief.")
                with gr.Accordion("Verified wall examples — wave / straight", open=True):
                    wave_button = gr.Button("Load connected wave wall — 100 pieces")
                    tested = gr.Button("Load tested 105-piece wall")
                    gr.Markdown("Connected wave: 8 staggered courses, 100 pieces including half-brick ends, ±20° rotation. Straight: 105 pieces. These buttons load saved designs; the text box is not used. Then click **2. Build reviewed design**.")
                questions = gr.Markdown(visible=False)
                answer = gr.Textbox(label="Your answer / accepted changes", lines=3, visible=False)
                resume = gr.Button("Continue with my answer", visible=False)
            with gr.Column(scale=2):
                live = gr.Image(value=idle_robot_preview(), label="Robot workspace — idle preview; live camera during construction", interactive=False, elem_id="robot-workspace")
                animation = gr.Image(value=saved_preview, label="Last verified wall preview — generate to build in this session" if saved_preview else "Wall preview — generate a design", type="filepath", interactive=False, visible=True, elem_id="wall-preview")
        with gr.Tab("Team decisions and live events"):
            log = gr.Textbox(lines=14, label="Agent decisions — provider and role recorded", interactive=False)
            timeline = gr.Textbox(lines=14, label="Verification / motion / scan / correction timeline", interactive=False)
        with gr.Tab("Scan vs BIM and memory"):
            heatmap = gr.Image(label="Latest measured scan deviations", interactive=False)
            deviations = gr.Dataframe(headers=["Brick", "dx mm", "dy mm", "dz mm", "yaw deg", "Within tolerance", "Unobserved"], label="Latest scan (updated after each scan)", interactive=False)
            memory = gr.JSON(label="New evidence-backed rules — no guarantee of preventing recurrence")
        with gr.Tab("Verification and report"):
            review = gr.JSON(label="Design and sequence")
            report = gr.JSON(label="Build report")
            video = gr.Video(label="Optional recording of this run", interactive=False)
        if sample.exists():
            with gr.Accordion("Archived offline example — not the current run", open=False):
                gr.Image(value=str(sample), label="Recorded fixture demonstration", interactive=False)
        outputs = [state, animation, review, log, status, build, timeline, questions, answer, resume, prepare]
        prepare.click(prepare_callback, [brief, variant, seed, provider, model], outputs, concurrency_id="simulation", concurrency_limit=1)
        def load_tested(brief: str, variant: str, seed: int, provider: str, model: str) -> Iterator[tuple]:
            yield from prepare_callback(brief, variant, seed, provider, model, tested_wall=True)
        tested.click(load_tested, [brief, variant, seed, provider, model], outputs, concurrency_id="simulation", concurrency_limit=1)
        def load_wave(brief: str, variant: str, seed: int, provider: str, model: str) -> Iterator[tuple]:
            yield from prepare_callback(brief, variant, seed, provider, model, tested_wall="wave")
        wave_button.click(load_wave, [brief, variant, seed, provider, model], outputs, concurrency_id="simulation", concurrency_limit=1)
        resume.click(answered_brief, [brief, answer], [brief], queue=False).success(
            prepare_callback, [brief, variant, seed, provider, model], outputs, concurrency_id="simulation", concurrency_limit=1)
        brief.input(lambda: (None, gr.update(interactive=False), gr.update(value=None, visible=True),
                            "Request changed. Generate and verify this request before building."),
                    outputs=[state, build, animation, status], queue=False)
        build.click(build_callback, [state, scenario, record], [live, heatmap, deviations, log, timeline, memory, report, status, video], concurrency_id="simulation", concurrency_limit=1)
    return app
