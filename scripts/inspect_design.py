"""Compile JSON and save verifier diagnostics: python scripts/inspect_design.py program.json."""
import argparse
import json
from pathlib import Path
from masonmind.config import load_config
from masonmind.schemas import DesignProgram
from masonmind.design.compiler import compile_program
from masonmind.design.verifier import verify
from masonmind.design.feedback import repair_hints
from masonmind.design.inspection import inspect_views
from masonmind.io import save_json


def main() -> None:
    """Inspect in the default UI workcell, or a supplied YAML configuration."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program",type=Path)
    parser.add_argument("--output",type=Path,default=Path("runs/inspection"))
    parser.add_argument("--config",type=Path)
    args=parser.parse_args()
    cfg=load_config(path=args.config) if args.config else load_config()
    if args.config is None:
        cfg["geometry"].update(load_config(path=Path("configs/ui_workspace.yaml"))["geometry"])
    program=DesignProgram.model_validate(json.loads(args.program.read_text(encoding="utf-8-sig")))
    design=compile_program(program,cfg)
    report=verify(design,cfg)
    inspect_views(design,report,cfg,args.output)
    save_json(args.output/"verification.json",report)
    save_json(args.output/"design.json",design)
    if not report.passed:
        save_json(args.output/"hints.json",repair_hints(program,cfg))
    print(f"{'PASS' if report.passed else 'FAIL'}: {len([b for b in design.bricks if not b.is_opening])} solid bricks, {len(report.issues)} issues; views: {args.output.resolve()}")


if __name__ == "__main__":
    main()
