import numpy as np
import pybullet as p
import pytest
from masonmind.config import load_config
from masonmind.sim.env import World
from masonmind.sim.robot import Panda


def test_sleeping_brick_still_falls_and_wakes_on_contact():
    cfg = load_config()
    cfg['sim']['enable_sleeping'] = True
    with World(cfg, robot=False) as world:
        target = world.spawn_brick('target', [.48, 0, .20])
        world.step(1200)
        before = np.asarray(p.getBasePositionAndOrientation(target, physicsClientId=world.cid)[0])
        assert abs(before[2] - .048) < .001
        striker = world.box([.04, .04, .04], [.36, 0, .061], mass=.2)
        p.resetBaseVelocity(striker, [2., 0., 0.], physicsClientId=world.cid)
        world.step(120)
        after = np.asarray(p.getBasePositionAndOrientation(target, physicsClientId=world.cid)[0])
        assert after[0] > before[0] + .01


def test_depth_monitor_omits_rgb_without_changing_measurements():
    from masonmind.sim.camera import Camera
    with World(robot=False) as world:
        world.spawn_brick("sensor_check")
        camera = Camera(world, resolution=(80, 60))
        full = camera.capture()
        depth_only = camera.capture(include_rgb=False)
        assert depth_only.rgb.shape == (0, 0, 3)
        assert np.array_equal(full.depth, depth_only.depth)
        assert np.array_equal(full.points, depth_only.points)
        assert np.array_equal(full.body_ids, depth_only.body_ids)


def test_ik_recovers_local_solver_stall_without_moving_live_arm(monkeypatch):
    with World() as world:
        arm = Panda(world)
        before = arm.state().copy()
        solver = p.calculateInverseKinematics
        calls = 0

        def stalled_first_seed(*args, **kwargs):
            nonlocal calls
            calls += 1
            return [0.] * 9 if calls <= 4 else solver(*args, **kwargs)

        monkeypatch.setattr(p, "calculateInverseKinematics", stalled_first_seed)
        target = [0.35, -0.30, 0.156]
        solution = arm.ik(target)
        assert calls == 8
        assert np.allclose(arm.state(), before)
        assert np.all(solution >= arm.lower) and np.all(solution <= arm.upper)
        for joint, value in zip(arm.joints, solution):
            p.resetJointState(arm.body, joint, value, physicsClientId=world.cid)
        assert np.linalg.norm(np.array(arm.pose()[0]) - target) <= world.sim["ik_tolerance"]


@pytest.mark.parametrize("clearance,yaw", [(0., 0.), (.001, 20.)])
def test_panda_pick_place(clearance, yaw):
    cfg = load_config()
    cfg['sim']['placement_clearance_m'] = clearance
    with World(cfg) as world:
        arm = Panda(world)
        body = world.spawn_brick("test")
        world.step(cfg["sim"]["settle_steps"])
        target = [0.48, 0, 0.048]
        arm.pick_place(body, cfg["sim"]["supply"], target, yaw_deg=yaw)
        actual, orientation = p.getBasePositionAndOrientation(body, physicsClientId=world.cid)
        assert np.linalg.norm(np.array(actual) - target) < 0.008
        assert abs(np.rad2deg(p.getEulerFromQuaternion(orientation)[2]) - yaw) < 2
        assert abs(actual[2] - target[2]) < .0005
        assert arm.held is None


def test_contact_motion_keeps_normal_limits_in_fast_profile(tmp_path):
    from masonmind.planning.collision import CollisionChecker
    from masonmind.planning.executor import MonitoredExecutor
    cfg = load_config()
    cfg['planning'].update(velocity=4.8, acceleration=54.0,
                           contact_velocity=0.8, contact_acceleration=1.5)
    with World(cfg) as world:
        arm = Panda(world)
        controller = MonitoredExecutor(CollisionChecker(arm, []), lambda: [], tmp_path / 'motion.jsonl')
        start = arm.state()
        goal = start.copy()
        goal[0] += 0.1
        arm.phase = 'place'
        before = world.t
        controller.execute([start, goal])
        contact_duration = world.t - before
        arm.phase = 'transfer'
        before = world.t
        controller.execute([arm.state(), start])
        free_duration = world.t - before
        assert contact_duration > free_duration + 0.25
