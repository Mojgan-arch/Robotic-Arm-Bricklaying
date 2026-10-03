"""Collision-preserving shortcut smoothing."""
from collections.abc import Callable
import numpy as np
from masonmind.planning.rrt_connect import edge_free


def shortcut(path: list[np.ndarray], valid: Callable[[np.ndarray], bool],
             resolution: float, iterations: int, seed: int = 0) -> list[np.ndarray]:
    """Replace random subpaths only after validating the whole shortcut."""
    result = [q.copy() for q in path]
    rng = np.random.default_rng(seed)
    for _ in range(iterations):
        if len(result) < 3:
            break
        i, j = sorted(rng.choice(len(result), 2, replace=False))
        if j > i + 1 and edge_free(result[i], result[j], valid, resolution):
            result = result[:i + 1] + result[j:]
    return result
