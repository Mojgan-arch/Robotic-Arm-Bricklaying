"""Offline wall-clock benchmark including live display, scans and physical placement."""
import argparse
import cProfile
import json
import pstats
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
    """Keep identical deterministic inputs and save measured outcomes."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='baseline')
    parser.add_argument('--speed', type=float, default=1)
    parser.add_argument('--scenario', default='clean')
    parser.add_argument('--large', action='store_true')
    parser.add_argument('--program', type=Path)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--contact-limits', action='store_true')
    parser.add_argument('--continuous', action='store_true')
    parser.add_argument('--sleeping', action='store_true')
    parser.add_argument('--no-live', action='store_true')
    args = parser.parse_args()
    cfg = load_config()
    cfg['memory']['enabled'] = False
    cfg['planning']['continuous_retiming'] = args.continuous
    cfg['sim']['enable_sleeping'] = args.sleeping
    cfg['ui']['stream_frames'] = not args.no_live
    cfg['planning']['velocity'] *= args.speed
    cfg['planning']['acceleration'] *= args.speed ** 2
    if args.contact_limits:
        cfg['planning']['contact_velocity'] = load_config()['planning']['velocity']
        cfg['planning']['contact_acceleration'] = load_config()['planning']['acceleration']
    if args.profile:
        for section, overrides in load_config(path=args.profile).items():
            cfg[section].update(overrides)
    owner = Orchestrator(cfg, load_config('llm'))
    frames = 0
    last_frame = None
    def observe(event: dict) -> None:
        nonlocal frames, last_frame
        if event['kind'] == 'frame':
            frames += 1
            last_frame = event['image']
        elif event['kind'] == 'brick_placed':
            print(event['placed'], flush=True)
    owner.observer = observe
    print('RUN', owner.run, flush=True)
    if args.large or args.program:
        cfg['geometry']['wall_yaw_deg'] = 90
        program = DesignProgram.model_validate_json((args.program or Path('configs/examples/large_studio_wall.json')).read_text())
    else:
        program = DesignProgram(n_courses=3, bricks_per_course=4, bond='stack', seed=7, rationale='Runtime benchmark')
    design = compile_program(program, cfg)
    sequence = plan_sequence(design, SequenceStrategy(strategy='left_to_right', rationale='Benchmark'), cfg)
    verification = verify(design, cfg)
    save_json(owner.run / 'verification_v1.json', verification)
    save_json(owner.run / 'design_v1.json', design)
    save_json(owner.run / 'program_v1.json', design.program)
    save_json(owner.run / 'sequence_v1.json', sequence)
    if not verification.passed or not sequence.report.passed:
        raise RuntimeError(f'Design/sequence rejected; inspect {owner.run}')
    profiler = cProfile.Profile()
    profiler.enable()
    report = owner.run_phase_b(design, sequence, args.scenario, 'B3', False)
    profiler.disable()
    if last_frame is not None:
        Image.fromarray(last_frame).save(owner.run / 'final_live_frame.png')
    profiler.dump_stats(str(owner.run / 'runtime.prof'))
    with (owner.run / 'runtime.txt').open('w') as stream:
        pstats.Stats(profiler, stream=stream).sort_stats('cumtime').print_stats(35)
    result = {**report.model_dump(), 'frames': frames, 'run': str(owner.run)}
    save_json(Path('runs') / f'runtime_{args.label}.json', result)
    print(json.dumps(result), flush=True)
    if not report.completed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
