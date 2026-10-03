import numpy as np
import pytest
import pybullet as p
from masonmind.config import load_config
from masonmind.planning.rrt_connect import rrt_connect, edge_free, PlanningError
from masonmind.planning.smoothing import shortcut
from masonmind.planning.time_param import parameterize
from masonmind.planning.collision import CollisionChecker
from masonmind.sim.env import World
from masonmind.sim.robot import Panda


def test_rrt_routes_around_barrier():
    cfg = load_config()["planning"]
    valid = lambda q: not (abs(q[0]) < .18 and abs(q[1]) < .65)
    start, goal = np.array([-.8, 0]), np.array([.8, 0])
    path = rrt_connect(start, goal, np.array([-1, -1]), np.array([1, 1]), valid, cfg, 3)
    path = shortcut(path, valid, cfg["edge_resolution"], 50, 3)
    assert np.allclose(path[0], start) and np.allclose(path[-1], goal)
    assert len(path) > 2
    assert all(edge_free(a, b, valid, cfg["edge_resolution"]) for a, b in zip(path, path[1:]))


def test_planner_fails_closed():
    with pytest.raises(PlanningError):
        rrt_connect(np.zeros(7), np.ones(7), -np.ones(7), np.ones(7), lambda q: False, load_config()["planning"])


def test_trapezoid_respects_limits_and_endpoints():
    trajectory = parameterize([np.zeros(7), np.array([1, .2, -.4, 0, .8, 0, 0]), np.zeros(7)], .8, 1.5, .005)
    assert np.all(np.diff(trajectory.t) > 0)
    assert np.max(np.abs(trajectory.dq)) <= .8 + 1e-10
    acceleration = np.diff(trajectory.dq, axis=0) / np.diff(trajectory.t)[:, None]
    assert np.max(np.abs(acceleration)) <= 1.5 + 1e-8
    assert np.allclose(trajectory.q[-1], 0)


def test_checker_restores_robot_and_detects_obstacle():
    with World() as world:
        arm = Panda(world)
        before = arm.state()
        checker = CollisionChecker(arm, [world.table])
        assert checker(before), checker.last_collision
        obstacle = world.box([.1, .1, .1], list(arm.pose()[0]))
        checker.scene.append(obstacle)
        assert not checker(before)
        assert np.allclose(arm.state(), before)


def test_held_brick_is_checked_and_restored():
    with World() as world:
        arm = Panda(world)
        position, orientation = arm.pose()
        carried = world.box([.40, .02, .02], (np.array(position) + [0, 0, -.03]).tolist(), mass=.035)
        original = p.getBasePositionAndOrientation(carried, physicsClientId=world.cid)
        arm.held = carried
        arm.hold_transform = p.multiplyTransforms(*p.invertTransform(position, orientation), *original)
        obstacle = world.box([.02, .02, .02], (np.array(position) + [.18, 0, -.03]).tolist())
        checker = CollisionChecker(arm, [obstacle])
        assert not checker(arm.state())
        assert "held brick" in checker.last_collision
        assert np.allclose(p.getBasePositionAndOrientation(carried, physicsClientId=world.cid)[0], original[0])
