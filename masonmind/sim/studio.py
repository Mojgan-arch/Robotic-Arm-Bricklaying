"""Procedural studio visuals; no extra collision geometry or sensor truth."""
from pathlib import Path
from PIL import Image, ImageDraw
import pybullet as p
from masonmind.config import ROOT


def apply_studio(cid: int, plane: int, robot: int | None) -> None:
    """Use existing articulated Panda meshes with a dark technical grid floor."""
    target = Path(ROOT) / "runs/web/studio_grid.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        canvas = Image.new("RGB", (512, 512), (21, 42, 58))
        draw = ImageDraw.Draw(canvas)
        for k in range(0, 512, 64):
            color = (98, 130, 145) if k == 0 else (55, 84, 103)
            draw.line((k, 0, k, 511), fill=color, width=2)
            draw.line((0, k, 511, k), fill=color, width=2)
        canvas.save(target)
    texture = p.loadTexture(str(target), physicsClientId=cid)
    p.changeVisualShape(plane, -1, textureUniqueId=texture, rgbaColor=[1, 1, 1, 1], physicsClientId=cid)
    if robot is not None:
        for link in [-1, *range(p.getNumJoints(robot, physicsClientId=cid))]:
            color = [.18, .22, .26, 1] if link in (6, 8, 9, 10) else [.78, .81, .83, 1]
            p.changeVisualShape(robot, link, rgbaColor=color, specularColor=[.7, .7, .7], physicsClientId=cid)
