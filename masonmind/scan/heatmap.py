"""Dependency-light deviation plots for run reports and the visual critic."""
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from masonmind.schemas import BrickDeviation


def heatmap(deviations: list[BrickDeviation], path: Path, tolerance_mm: float = 3) -> None:
    """Render labelled deviation bars, with explicit missing-observation markers."""
    image = Image.new("RGB", (720, max(100, 45 + 26 * len(deviations))), "#0f172a")
    draw = ImageDraw.Draw(image)
    draw.text((16, 12), f"Scan-vs-BIM | translation norm (mm) | tolerance {tolerance_mm:g} mm", fill="white")
    values = [float(np.linalg.norm([d.dx_mm, d.dy_mm, d.dz_mm])) for d in deviations]
    scale = 380 / max(tolerance_mm * 2, max(values, default=0))
    for index, (deviation, value) in enumerate(zip(deviations, values)):
        y = 40 + index * 26
        draw.text((16, y), deviation.brick_id, fill="white")
        color = "#22c55e" if deviation.within_tolerance else "#ef4444"
        if deviation.missing:
            draw.text((130, y), "UNOBSERVED", fill="#fbbf24")
        else:
            draw.rectangle((130, y, 130 + max(1, value * scale), y + 16), fill=color)
            draw.text((530, y), f"{value:.2f} mm / {deviation.dyaw_deg:+.2f} deg", fill="white")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
