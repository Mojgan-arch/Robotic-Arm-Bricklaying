"""Synchronized trapezoidal profiles with zero velocity at each waypoint."""
from dataclasses import dataclass
import numpy as np


@dataclass
class Trajectory:
    t: np.ndarray
    q: np.ndarray
    dq: np.ndarray


def parameterize(path: list[np.ndarray], velocity: float | np.ndarray, acceleration: float | np.ndarray,
                 dt: float) -> Trajectory:
    """Map each joint segment to a bounded trapezoid (triangle for short moves)."""
    if not path or dt <= 0 or np.any(np.asarray(velocity) <= 0) or np.any(np.asarray(acceleration) <= 0):
        raise ValueError("A nonempty path and positive motion limits are required")
    times, positions, velocities = [0.0], [np.asarray(path[0]).copy()], [np.zeros_like(path[0])]
    for start, end in zip(path, path[1:]):
        delta = np.asarray(end) - start
        moving = np.abs(delta) > 1e-12
        if not np.any(moving):
            continue
        vmax = float(np.min(np.broadcast_to(velocity, delta.shape)[moving] / np.abs(delta[moving])))
        amax = float(np.min(np.broadcast_to(acceleration, delta.shape)[moving] / np.abs(delta[moving])))
        ramp = min(vmax / amax, np.sqrt(1 / amax))
        peak = amax * ramp
        cruise = max(0.0, (1 - amax * ramp ** 2) / peak)
        duration = 2 * ramp + cruise
        local_times = np.linspace(0, duration, max(1, int(np.ceil(duration / dt))) + 1)[1:]
        offset = times[-1]
        for t in local_times:
            if t < ramp:
                s, speed = amax * t ** 2 / 2, amax * t
            elif t < ramp + cruise:
                s, speed = amax * ramp ** 2 / 2 + peak * (t - ramp), peak
            else:
                remaining = duration - t
                s, speed = 1 - amax * remaining ** 2 / 2, amax * remaining
            times.append(offset + t)
            positions.append(np.asarray(start) + s * delta)
            velocities.append(speed * delta)
    return Trajectory(np.asarray(times), np.asarray(positions), np.asarray(velocities))
