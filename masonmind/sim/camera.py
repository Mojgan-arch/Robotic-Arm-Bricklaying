"""RGB-D capture and OpenGL-depth unprojection."""
from dataclasses import dataclass
import numpy as np
import pybullet as p
from masonmind.sim.env import World


@dataclass
class Frame:
    rgb: np.ndarray
    depth: np.ndarray
    segmentation: np.ndarray
    points: np.ndarray
    body_ids: np.ndarray


class Camera:
    """A calibrated virtual camera with seeded sensor noise."""

    def __init__(self, world: World, eye: list[float] | None = None):
        self.world = world
        cfg = world.sim
        self.width, self.height = cfg["camera_width"], cfg["camera_height"]
        self.view = p.computeViewMatrix(eye or cfg["camera_eye"], cfg["camera_target"], [0, 0, 1])
        self.projection = p.computeProjectionMatrixFOV(cfg["camera_fov"], self.width / self.height,
                                                       cfg["camera_near"], cfg["camera_far"])
        self.inverse = np.linalg.inv(np.array(self.projection).reshape(4, 4, order="F") @
                                     np.array(self.view).reshape(4, 4, order="F"))
        self.rng = np.random.default_rng(world.config["seed"])

    def capture(self, unproject: bool = True) -> Frame:
        """Return pixels and only visible surface points, without pose queries."""
        _, _, rgba, depth, segmentation = p.getCameraImage(
            self.width, self.height, self.view, self.projection,
            renderer=p.ER_TINY_RENDERER, flags=p.ER_SEGMENTATION_MASK_OBJECT_AND_LINKINDEX,
            physicsClientId=self.world.cid)
        depth = np.asarray(depth).reshape(self.height, self.width)
        segmentation = np.asarray(segmentation).reshape(self.height, self.width)
        rgb = np.asarray(rgba, dtype=np.uint8).reshape(self.height, self.width, 4)[..., :3]
        if not unproject:
            return Frame(rgb, depth, segmentation, np.empty((0, 3)), np.empty(0, dtype=int))
        yy, xx = np.mgrid[:self.height, :self.width]
        # TinyRenderer uses integer X samples and bottom-up Y raster rows.
        # The Y+1 convention is verified against independently known box surfaces.
        clip = np.stack([2 * xx / self.width - 1,
                         1 - 2 * (yy + 1) / self.height,
                         2 * depth - 1, np.ones_like(depth)], axis=-1)
        homogeneous = clip @ self.inverse.T
        points = homogeneous[..., :3] / homogeneous[..., 3:]
        noise = self.world.config["scan"]["depth_noise_m"]
        if noise:
            points += self.rng.normal(0, noise, points.shape)
        ids = np.where(segmentation < 0, -1, segmentation & ((1 << 24) - 1))
        valid = (depth < 1) & (ids >= 0)
        return Frame(rgb,
                     depth, segmentation, points[valid], ids[valid])
