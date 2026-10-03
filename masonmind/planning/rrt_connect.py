"""Small bidirectional RRT-Connect implementation."""
from collections.abc import Callable
from dataclasses import dataclass
import time
import numpy as np


class PlanningError(RuntimeError):
    """No admissible path was found within the configured search budget."""


def edge_free(a: np.ndarray, b: np.ndarray, valid: Callable[[np.ndarray], bool], resolution: float) -> bool:
    """Check endpoints and uniformly sampled configurations along an edge."""
    count = max(1, int(np.ceil(np.linalg.norm(b - a) / resolution)))
    return all(valid(a + t * (b - a)) for t in np.linspace(0, 1, count + 1))


@dataclass
class Tree:
    nodes: list[np.ndarray]
    parents: list[int]

    def trace(self, index: int) -> list[np.ndarray]:
        """Return the root-to-node path."""
        result = []
        while index >= 0:
            result.append(self.nodes[index])
            index = self.parents[index]
        return result[::-1]


def rrt_connect(start: np.ndarray, goal: np.ndarray, lower: np.ndarray, upper: np.ndarray,
                valid: Callable[[np.ndarray], bool], config: dict, seed: int = 0) -> list[np.ndarray]:
    """Connect two collision-free joint configurations or raise PlanningError."""
    start, goal = np.asarray(start, dtype=float), np.asarray(goal, dtype=float)
    if not valid(start) or not valid(goal):
        raise PlanningError("Start or goal configuration is in collision or outside joint limits")
    resolution = config["edge_resolution"]
    if edge_free(start, goal, valid, resolution):
        return [start, goal]
    rng = np.random.default_rng(seed)
    a, b = Tree([start], [-1]), Tree([goal], [-1])
    swapped = False
    deadline = time.perf_counter() + config["timeout_s"]

    def extend(tree: Tree, target: np.ndarray) -> tuple[int, bool] | None:
        nearest = int(np.argmin([np.linalg.norm(q - target) for q in tree.nodes]))
        delta = target - tree.nodes[nearest]
        distance = np.linalg.norm(delta)
        if distance < 1e-10:
            return nearest, True
        candidate = tree.nodes[nearest] + delta * min(1, config["step_size"] / distance)
        if not edge_free(tree.nodes[nearest], candidate, valid, resolution):
            return None
        tree.nodes.append(candidate)
        tree.parents.append(nearest)
        return len(tree.nodes) - 1, distance <= config["step_size"]

    for _ in range(config["max_iterations"]):
        if time.perf_counter() >= deadline:
            break
        extended = extend(a, rng.uniform(lower, upper))
        if extended:
            ai, _ = extended
            while time.perf_counter() < deadline:
                connected = extend(b, a.nodes[ai])
                if connected is None:
                    break
                bi, reached = connected
                if reached:
                    path = a.trace(ai) + b.trace(bi)[-2::-1]
                    return path[::-1] if swapped else path
        a, b = b, a
        swapped = not swapped
    raise PlanningError(f"RRT-Connect exhausted {config['max_iterations']} iterations or {config['timeout_s']} seconds")
