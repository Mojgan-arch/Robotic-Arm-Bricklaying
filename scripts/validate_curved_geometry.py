"""Offline evidence for curved geometry; does not certify a complete build."""
from pathlib import Path
from PIL import Image
from masonmind.config import load_config
from masonmind.schemas import DesignProgram
from masonmind.design.compiler import compile_program
from masonmind.design.verifier import verify
from masonmind.sim.stability import drop_test
from masonmind.sim.env import World
from masonmind.sim.camera import Camera
from masonmind.orchestrator import Orchestrator
from masonmind.io import save_json


def main() -> None:
    """Measure a supported arc and preserve rejection of the oversized perforation."""
    cfg = load_config()
    cfg["geometry"]["wall_yaw_deg"] = 90
    cfg["memory"]["enabled"] = False
    root = Path("runs/curved_geometry")
    root.mkdir(parents=True, exist_ok=True)
    region = {"course_start": 0, "course_end": 2}
    program = DesignProgram(n_courses=3, bricks_per_course=6, bond="stack", seed=7,
        rationale="Offline geometry acceptance: arc with lengthwise yaw gradient",
        arc_angle_deg=20, rotation_rules=[{"region": region, "kind": "length_gradient", "amplitude_deg": 15}])
    design = compile_program(program, cfg)
    geometric = verify(design, cfg)
    stability = drop_test(design.bricks, cfg)
    owner = Orchestrator(cfg, run=root)
    reach = owner.reachability(design)
    save_json(root / "acceptance.json", {"program": program, "geometry": geometric,
              "stability": stability, "reachability": reach, "full_build_tested": False})
    with World(cfg, robot=False) as world:
        for b in design.bricks:
            world.spawn_brick(b.id, [b.x, b.y, b.z], b.yaw_deg)
        Image.fromarray(Camera(world).capture(unproject=False).rgb).save(root / "preview.png")
    assert geometric.passed, geometric
    assert stability.passed, stability
    assert reach["passed"], reach
    print("Curved 18-brick gradient: geometry, drop test and target reachability passed.")


if __name__ == "__main__":
    main()
