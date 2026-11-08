"""Reproduce the requested gap/rotation case without model variability."""
from pathlib import Path
from collections import Counter
from PIL import Image
from masonmind.config import load_config
from masonmind.schemas import DesignProgram
from masonmind.design.compiler import compile_program
from masonmind.design.verifier import verify
from masonmind.design.feedback import compact_issues, repair_hints
from masonmind.sim.env import World
from masonmind.sim.camera import Camera
from masonmind.io import save_json


def main() -> None:
    """Save measured geometry results, including unsupported ends."""
    cfg = load_config()
    cfg["geometry"]["wall_yaw_deg"] = 90
    region = {"course_start": 0, "course_end": 2}
    p = DesignProgram(n_courses=3, bricks_per_course=6, bond="running", gap_ratio=.33,
        seed=7, rationale="Requested hit-and-miss validation; straight plan, 0–20 degree yaw gradient",
        rotation_rules=[{"region":region,"kind":"length_gradient","amplitude_deg":20}],
        opening_rules=[{"region":region,"kind":"hit_and_miss"}])
    design=compile_program(p,cfg)
    report=verify(design,cfg)
    root=Path("runs/bridge_wall_033_20")
    root.mkdir(parents=True,exist_ok=True)
    save_json(root/"program.json",p)
    save_json(root/"issues.json",report)
    failed={name for issue in report.issues for name in issue.brick_ids}
    result={"passed":report.passed,"solid_bricks":sum(not b.is_opening for b in design.bricks),
        "failed_bricks":sorted(failed),"issue_counts":dict(Counter(i.check for i in report.issues)),
        "summary":compact_issues(design,report),"hints":repair_hints(p,cfg),
        "physics_or_build_tested":False}
    save_json(root/"result.json",result)
    with World(cfg,robot=False) as world:
        for b in design.bricks:
            if not b.is_opening:
                world.spawn_brick(b.id,[b.x,b.y,b.z],b.yaw_deg)
        Image.fromarray(Camera(world).capture(unproject=False).rgb).save(root/"preview.png")
    print(result)


if __name__ == "__main__":
    main()
