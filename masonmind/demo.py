"""Milestone demos with measurable artifacts."""
from argparse import Namespace
import time
import numpy as np
import pybullet as p
from masonmind.config import load_config
from masonmind.io import create_run, save_json
from masonmind.sim.env import World
from masonmind.sim.robot import Panda
from masonmind.sim.camera import Camera
from masonmind.sim.recorder import Recorder


def run_demo(args: Namespace) -> None:
    """Execute the requested acceptance demonstration."""
    if args.milestone == 2:
        design_demo(args)
        return
    if args.milestone in (3, 4):
        baseline_demo(args)
        return
    if args.milestone != 1:
        raise ValueError("Only milestone 1 is implemented at this development stage")
    config = load_config()
    config["seed"], config["sim"]["gui"] = args.seed, args.gui
    run = create_run()
    save_json(run / "config.json", config)
    started = time.perf_counter()
    with World(config) as world:
        arm = Panda(world)
        camera = Camera(world)
        recorder = None if args.no_video else Recorder(camera, run / "video.mp4")
        results = []
        try:
            for index in range(5):
                target = [0.48, (index - 2) * 0.065, 0.048]
                body = world.spawn_brick(f"c00_b{index:02}")
                world.step(config["sim"]["settle_steps"])
                # A supply fixture defines the commanded source pose.
                arm.pick_place(body, config["sim"]["supply"], target, 90)
                position, _ = p.getBasePositionAndOrientation(body, physicsClientId=world.cid)
                results.append({"target": target, "actual": position,
                                "error_mm": float(np.linalg.norm(np.array(position) - target) * 1000)})
            from PIL import Image
            Image.fromarray(camera.capture().rgb).save(run / "final.png")
        finally:
            if recorder:
                recorder.close()
        save_json(run / "report.json", {"milestone": 1, "bricks": results,
                                        "duration_s": time.perf_counter() - started})
    print(f"Run saved to {run.resolve()}")


def design_demo(args: Namespace) -> None:
    """Compile and verify three distinct pattern programs and save previews."""
    from masonmind.schemas import DesignProgram, RotationRule, Region
    from masonmind.design.compiler import compile_program
    from masonmind.design.verifier import verify
    from PIL import Image
    config = load_config()
    config["geometry"]["pitch"] = 0.062
    run = create_run()
    for kind in ("constant", "wave", "alternate"):
        program = DesignProgram(n_courses=3, bricks_per_course=6, bond="stack", seed=args.seed,
            rotation_rules=[RotationRule(region=Region(course_start=0, course_end=2), kind=kind, amplitude_deg=20)],
            rationale=f"{kind} pattern demonstration")
        design = compile_program(program, config)
        report = verify(design, config)
        if not report.passed:
            raise RuntimeError(report.model_dump_json())
        save_json(run / f"{kind}_design.json", design)
        save_json(run / f"{kind}_verification.json", report)
        with World(config, robot=False) as world:
            for brick in design.bricks:
                world.spawn_brick(brick.id, [brick.x, brick.y, brick.z], brick.yaw_deg)
            Image.fromarray(Camera(world).capture(unproject=False).rgb).save(run / f"{kind}.png")
    print(f"Three verified designs: {run.resolve()}")


def baseline_demo(args: Namespace) -> None:
    """Physically build two full walls with different verified orders."""
    from masonmind.schemas import DesignProgram, SequenceStrategy
    from masonmind.design.compiler import compile_program
    from masonmind.design.sequencer import plan_sequence
    from masonmind.design.verifier import verify
    from PIL import Image
    config = load_config()
    config["sim"]["gui"] = args.gui
    design = compile_program(DesignProgram(n_courses=3, bricks_per_course=4, bond="stack", seed=args.seed, rationale="B0"), config)
    if not verify(design, config).passed:
        raise RuntimeError("Baseline geometry failed")
    for name in ("left_to_right", "right_to_left"):
        run = create_run()
        sequence = plan_sequence(design, SequenceStrategy(strategy=name, rationale="Baseline comparison"), config)
        if not sequence.report.passed:
            raise RuntimeError(sequence.report.model_dump_json())
        save_json(run / "design.json", design)
        save_json(run / "sequence.json", sequence)
        with World(config) as world:
            arm = Panda(world)
            controller = None
            collision_count = [0]
            if args.milestone == 4:
                from masonmind.planning.collision import CollisionChecker
                from masonmind.planning.controller import StaticController
                obstacle = world.box(config["planning"]["static_obstacle_size"], config["planning"]["static_obstacle_position"], color=(.2, .5, .7, 1))
                checker = CollisionChecker(arm, [world.table, obstacle])
                controller = StaticController(checker, run / "planning.jsonl")
                arm.plan, arm.execute = controller.plan, controller.execute
                def audit() -> None:
                    bodies = [arm.body] + ([] if arm.held is None else [arm.held])
                    if any(p.getContactPoints(b, obstacle, physicsClientId=world.cid) for b in bodies):
                        collision_count[0] += 1
                world.on_step.append(audit)
            by_id = {b.id: b for b in design.bricks}
            for name_id in sequence.order:
                brick = by_id[name_id]
                body = world.spawn_brick(name_id)
                if controller:
                    checker.scene.append(body)
                world.step(config["sim"]["settle_steps"])
                arm.pick_place(body, config["sim"]["supply"], [brick.x, brick.y, brick.z], brick.yaw_deg)
                if controller:
                    checker.support_bodies.add(body)
            Image.fromarray(Camera(world).capture(unproject=False).rgb).save(run / "final.png")
            errors = {b.id: float(np.linalg.norm(np.array(p.getBasePositionAndOrientation(world.bricks[b.id], physicsClientId=world.cid)[0]) -
                                                [b.x, b.y, b.z]) * 1000) for b in design.bricks}
            save_json(run / "report.json", {"variant": "B0" if not controller else "static_planner", "strategy": name,
                "placement_error_mm": errors, "obstacle_contact_ticks": collision_count[0],
                "planning_time_s": None if not controller else controller.durations})
        print(f"Built {name}: {run.resolve()}")
