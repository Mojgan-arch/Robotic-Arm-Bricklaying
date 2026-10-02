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
