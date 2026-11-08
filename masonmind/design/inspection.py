"""Noninteractive diagnostic projections, with actual solid footprints."""
from pathlib import Path
import math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as Patch, Rectangle, Circle
from shapely.affinity import rotate, translate
from masonmind.schemas import Design, VerificationReport
from masonmind.design.verifier import footprint


def inspect_views(design: Design, report: VerificationReport, config: dict, folder: Path) -> None:
    """Save top and front views; red denotes bricks with individual failures."""
    folder.mkdir(parents=True, exist_ok=True)
    cfg = config["geometry"]
    ox, oy, oz = cfg["origin"]
    yaw = cfg.get("wall_yaw_deg", 0)
    failed = {name for issue in report.issues for name in issue.brick_ids}
    whole = any(not i.brick_ids for i in report.issues)
    for view in ("top", "front"):
        fig, ax = plt.subplots(figsize=(11, 5))
        if view == "top":
            ax.add_patch(Rectangle((-cfg["max_length"]/2, -cfg["max_depth"]/2), cfg["max_length"], cfg["max_depth"], fill=False, edgecolor="blue", linestyle="--", label="length/depth envelope"))
            theta = math.radians(yaw)
            centre = (-ox*math.cos(theta)-oy*math.sin(theta), ox*math.sin(theta)-oy*math.cos(theta))
            ax.add_patch(Circle(centre, cfg["workspace_radius"], fill=False, edgecolor="purple", linestyle=":", label="radial limit (plan projection)"))
        else:
            ax.add_patch(Rectangle((-cfg["max_length"]/2, 0), cfg["max_length"], cfg["max_height"], fill=False, edgecolor="blue", linestyle="--", label="length/height envelope"))
        for b in design.bricks:
            if b.is_opening:
                continue
            poly = rotate(translate(footprint(b, cfg["brick_size"]), -ox, -oy), -yaw, origin=(0, 0))
            color = "red" if whole or b.id in failed else "#c97842"
            if view == "top":
                ax.add_patch(Patch(list(poly.exterior.coords), facecolor=color, edgecolor="black", alpha=.35))
            else:
                bounds = poly.bounds
                h = (b.size or cfg["brick_size"])[2]
                ax.add_patch(Rectangle((bounds[0], b.z-oz-h/2), bounds[2]-bounds[0], h, facecolor=color, edgecolor="black", alpha=.6))
        ax.autoscale_view()
        ax.set_aspect("equal")
        ax.set_xlabel("Along wall (m)")
        ax.set_ylabel("Depth (m)" if view == "top" else "Height (m)")
        ax.set_title(f"{view.title()} view — {'PASS' if report.passed else 'FAIL'}; red = failing bricks (all red for wall-wide failure)")
        ax.legend(loc="upper right")
        ax.grid(alpha=.2)
        fig.tight_layout()
        fig.savefig(folder / f"{view}.png", dpi=140)
        plt.close(fig)
