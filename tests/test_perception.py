import numpy as np
from masonmind.config import load_config
from masonmind.sim.env import World
from masonmind.sim.camera import Camera, Frame
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


def test_visible_extents_resolve_side_face_slide_without_hiding_real_error():
    cfg = load_config()
    cfg['scan']['extent_recentering'] = True
    brick = Brick(id='side_faces', course=0, index=0, x=.48, y=0, z=.048, yaw_deg=20)
    length, width, height = cfg['geometry']['brick_size']
    # Two depth views see opposite side faces, with complete length/height
    # extents but no end-face normals to constrain point-to-plane ICP sliding.
    local = np.array([[x, side * width / 2, z]
                      for side in [-1, 1] for x in np.linspace(-length/2, length/2, 31)
                      for z in np.linspace(-height/2, height/2, 11)])
    angle = np.deg2rad(20)
    rotation = np.array([[np.cos(angle), -np.sin(angle), 0],
                         [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
    for shift in [0., .004]:
        points = (local + [shift, 0, 0]) @ rotation.T + [brick.x, brick.y, brick.z]
        frame = Frame(np.empty(0), np.empty(0), np.empty(0), points, np.ones(len(points), dtype=int))
        result = scan_bricks([frame], [brick], {brick.id: 1}, cfg)
        delta = result.deviations[0]
        expected = np.array([shift, 0, 0]) @ rotation.T * 1000
        assert np.linalg.norm(np.array([delta.dx_mm, delta.dy_mm, delta.dz_mm]) - expected) < .2
        assert delta.within_tolerance == (shift == 0)
