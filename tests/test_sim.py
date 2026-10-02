import numpy as np
import pybullet as p
from masonmind.config import load_config
from masonmind.sim.env import World
from masonmind.sim.robot import Panda


def test_panda_pick_place():
    cfg = load_config()
    with World(cfg) as world:
        arm = Panda(world)
        body = world.spawn_brick("test")
        world.step(cfg["sim"]["settle_steps"])
        target = [0.48, 0, 0.048]
        arm.pick_place(body, cfg["sim"]["supply"], target)
        actual, _ = p.getBasePositionAndOrientation(body, physicsClientId=world.cid)
        assert np.linalg.norm(np.array(actual) - target) < 0.008
        assert arm.held is None
