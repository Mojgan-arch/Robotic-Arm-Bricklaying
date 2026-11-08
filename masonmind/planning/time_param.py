"""Conservative waypoint profiles and bounded sampled polyline retiming."""
from dataclasses import dataclass
import numpy as np


@dataclass
class Trajectory:
    t: np.ndarray
    q: np.ndarray
    dq: np.ndarray


def parameterize_continuous(path: list[np.ndarray], velocity: float, acceleration: float,
                            dt: float) -> Trajectory:
    """Traverse the original polyline without stopping at every IK sample.

    Retiming preserves every geometric segment. Finite-difference velocity and
    acceleration, including rest at both ends, are bounded at the control rate.
    This is a sampled controller guarantee, not continuous-time corner blending.
    """
    if not path or dt <= 0 or velocity <= 0 or acceleration <= 0:
        raise ValueError("A nonempty path and positive motion limits are required")
    points = np.asarray(path, dtype=float)
    lengths = np.max(np.abs(np.diff(points, axis=0)), axis=1)
    points = points[np.r_[True, lengths > 1e-12]]
    if len(points) == 1:
        return Trajectory(np.array([0.]), points.copy(), np.zeros_like(points))
    distances = np.r_[0., np.cumsum(np.max(np.abs(np.diff(points, axis=0)), axis=1))]
    duration = max(2 * distances[-1] / velocity, np.sqrt(6 * distances[-1] / acceleration), dt)
    for _ in range(30):
        count = int(np.ceil(duration / dt))
        times = np.arange(count + 1) * dt
        u = times / times[-1]
        progress = distances[-1] * (3 * u ** 2 - 2 * u ** 3)
        q = np.column_stack([np.interp(progress, distances, points[:, j]) for j in range(points.shape[1])])
        segment_velocity = np.diff(q, axis=0) / dt
        padded_velocity = np.vstack([np.zeros(points.shape[1]), segment_velocity, np.zeros(points.shape[1])])
        peak_v = np.max(np.abs(segment_velocity))
        peak_a = np.max(np.abs(np.diff(padded_velocity, axis=0) / dt))
        factor = max(peak_v / velocity, np.sqrt(peak_a / acceleration))
        if factor <= 1 + 1e-9:
            return Trajectory(times, q, np.vstack([np.zeros(points.shape[1]), segment_velocity]))
        duration = times[-1] * max(1.05, factor * 1.02)
    # Very sharp paths retain the conservative stop-at-waypoint implementation.
    return parameterize(path, velocity, acceleration, dt)


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
