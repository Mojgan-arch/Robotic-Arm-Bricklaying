"""Unknown-object detection strictly from calibrated depth and segmentation."""
from dataclasses import dataclass
import numpy as np
from scipy.spatial import cKDTree
from masonmind.sim.camera import Frame


@dataclass
class ObstacleBox:
    minimum: np.ndarray
    maximum: np.ndarray
    point_count: int

    @property
    def center(self) -> np.ndarray:
        return (self.minimum + self.maximum) / 2

    @property
    def size(self) -> np.ndarray:
        return self.maximum - self.minimum


def detect_obstacles(frames: list[Frame], known_ids: set[int], config: dict) -> list[ObstacleBox]:
    """Mask known bodies, voxelize, cluster and conservatively bound visible points.

    No simulator API or object poses are used. Bounding boxes are measurements of
    visible surfaces, padded for sensor discretization; occluded space is unknown.
    """
    groups = [f.points[~np.isin(f.body_ids, list(known_ids))] for f in frames]
    points = np.concatenate(groups) if groups else np.empty((0, 3))
    if not len(points):
        return []
    voxel = config["voxel_size_m"]
    _, indices = np.unique(np.floor(points / voxel).astype(int), axis=0, return_index=True)
    points = points[indices]
    tree = cKDTree(points)
    unseen = set(range(len(points)))
    result = []
    while unseen:
        first = min(unseen)
        unseen.remove(first)
        cluster, frontier = [first], [first]
        while frontier:
            current = frontier.pop()
            neighbours = set(tree.query_ball_point(points[current], config["cluster_radius"])) & unseen
            unseen.difference_update(neighbours)
            cluster.extend(sorted(neighbours))
            frontier.extend(sorted(neighbours))
        if len(cluster) >= config["min_cluster_points"]:
            cloud = points[cluster]
            padding = config["obstacle_padding_m"]
            result.append(ObstacleBox(cloud.min(axis=0) - padding, cloud.max(axis=0) + padding, len(cluster)))
    return result
