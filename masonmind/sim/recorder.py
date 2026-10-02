"""Portable MP4 output in DIRECT and GUI modes."""
from pathlib import Path
import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw
from masonmind.sim.camera import Camera


class Recorder:
    """Capture frames at simulated-time intervals; encode on close."""

    def __init__(self, camera: Camera, path: Path):
        self.camera = camera
        self.fps = camera.world.sim["video_fps"]
        path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = imageio.get_writer(str(path), fps=self.fps, codec="libx264", macro_block_size=1)
        self.next_t = 0.0
        self.state = "moving"
        self.distance: float | None = None
        self.closed = False
        camera.world.on_step.append(self.tick)

    def tick(self) -> None:
        """Write an annotated frame when the next frame is due."""
        if self.camera.world.t + 1e-9 < self.next_t:
            return
        frame = Image.fromarray(self.camera.capture(unproject=False).rgb)
        draw = ImageDraw.Draw(frame)
        draw.rectangle([0, 0, frame.width, 28], fill=(15, 23, 42))
        distance = "unmonitored" if self.distance is None else f"{self.distance:.3f} m"
        draw.text((10, 8), f"MasonMind | {self.state} | obstacle: {distance}", fill="white")
        self.writer.append_data(np.asarray(frame))
        self.next_t = self.camera.world.t + 1 / self.fps

    def close(self) -> None:
        """Flush the MP4 and unregister the callback."""
        if not self.closed:
            self.camera.world.on_step.remove(self.tick)
            self.writer.close()
            self.closed = True
