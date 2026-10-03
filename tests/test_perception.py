import numpy as np
from masonmind.config import load_config
from masonmind.sim.env import World
from masonmind.sim.camera import Camera
from masonmind.schemas import Brick
from masonmind.planning.perception import detect_obstacles
from masonmind.scan.registration import scan_bricks
from masonmind.scan.evaluation import evaluate_scan


def test_depth_obstacle_encloses_box():
    cfg = load_config()
    with World(cfg, robot=False) as world:
        center, size = np.array([.48, -.15, .10]), np.array([.06, .06, .12])
        world.box(size.tolist(), center.tolist())
        frames = [Camera(world, eye).capture() for eye in cfg["scan"]["camera_eyes"]]
        boxes = detect_obstacles(frames, world.known_ids, cfg["scan"])
        assert len(boxes) == 1
        assert np.all(boxes[0].minimum <= center - size / 2)
        assert np.all(boxes[0].maximum >= center + size / 2)


def test_depth_icp_accuracy_without_pose_input():
    cfg = load_config()
    with World(cfg, robot=False) as world:
        brick = Brick(id="test", course=0, index=0, x=.48, y=0, z=.048, yaw_deg=0)
        world.spawn_brick(brick.id, [.484, -.002, .048], 3)
        frames = [Camera(world, eye).capture() for eye in cfg["scan"]["camera_eyes"]]
        result = scan_bricks(frames, [brick], world.bricks, cfg)
        report = evaluate_scan([brick], result.deviations, world.bricks, world.cid)
        assert report["observed_count"] == 1
        assert report["mean_translation_error_mm"] < 1, report
        assert report["mean_yaw_error_deg"] < 1, report
        assert not result.deviations[0].within_tolerance
