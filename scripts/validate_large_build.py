"""Rebuild the previous 105-piece design with real local supervision."""
import argparse
import json
import faulthandler
from pathlib import Path
from PIL import Image
from masonmind.config import load_config
from masonmind.schemas import DesignProgram, SequenceStrategy
from masonmind.design.compiler import compile_program
from masonmind.design.verifier import verify
from masonmind.design.sequencer import plan_sequence
from masonmind.orchestrator import Orchestrator
from masonmind.io import save_json


def main() -> None:
    """Use measured execution; retain failures, never substitute a success result."""
    parser=argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["mock", "ollama"],default="mock")
    parser.add_argument("--program", type=Path, default=Path("configs/examples/large_studio_wall.json"))
    parser.add_argument("--trace-stalls", action="store_true")
    args=parser.parse_args()
    if args.trace_stalls:
        faulthandler.dump_traceback_later(60, repeat=True)
    cfg=load_config()
    cfg["ui"]["stream_frames"]=False  # headless acceptance; UI still streams normally
    cfg["geometry"]["wall_yaw_deg"]=90
    cfg["memory"]["enabled"]=False
    llm=load_config("llm_local" if args.provider=="ollama" else "llm")
    p=DesignProgram.model_validate(json.loads(args.program.read_text(encoding="utf-8")))
    owner=Orchestrator(cfg,llm)
    owner.brief="Rebuild the reviewed 105-piece straight running-bond wall, one-third gaps and half end bricks. This test does not claim solid top/bottom courses."
    def observer(event: dict) -> None:
        if event["kind"]=="frame":
            Image.fromarray(event["image"]).save(owner.run/"live.png")
        elif event["kind"] in {"brick_placed","agent_started","build_finished"}:
            print(event["kind"],event.get("placed",event.get("agent","")),flush=True)
    owner.observer=observer
    print("RUN",owner.run,flush=True)
    design=compile_program(p,cfg)
    verification=verify(design,cfg)
    save_json(owner.run/"verification_v1.json",verification)
    save_json(owner.run/"program_v1.json",p)
    save_json(owner.run/"design_v1.json",design)
    assert verification.passed
    sequence=plan_sequence(design,SequenceStrategy(strategy="left_to_right",rationale="Previously reviewed order"),cfg)
    assert sequence.report.passed
    save_json(owner.run/"sequence_v1.json",sequence)
    report=owner.run_phase_b(design,sequence,"clean","B3",False)
    save_json(Path(f"runs/large_build_{args.provider}.json"),report)
    print(report.model_dump_json(),flush=True)
    faulthandler.cancel_dump_traceback_later()
    if not report.completed:
        raise SystemExit(1)


if __name__=="__main__":
    main()
