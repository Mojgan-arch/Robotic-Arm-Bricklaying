import pytest
import pybullet as pb
from masonmind.config import load_config
from masonmind.schemas import DesignProgram
from masonmind.design.compiler import compile_program
from masonmind.design.verifier import verify, footprint
from masonmind.design.inspection import inspect_views
from masonmind.design.feedback import repair_hints
from masonmind.sim.env import World
from masonmind.sim.stability import drop_test


def case(treatment="half_brick"):
    cfg=load_config()
    cfg["geometry"]["wall_yaw_deg"]=90
    p=DesignProgram(n_courses=3,bricks_per_course=10,bond="running",gap_ratio=.33,
        end_treatment=treatment,seed=7,rationale="End closure regression",
        opening_rules=[{"region":{"course_start":0,"course_end":2},"kind":"hit_and_miss"}])
    return compile_program(p,cfg),cfg


def test_ten_brick_hit_miss_half_ends_pass():
    d,cfg=case()
    assert verify(d,cfg).passed
    assert len(d.bricks)==31  # 10 full + (9 full, 2 halves) + 10 full
    halves=[b for b in d.bricks if b.brick_type=="half"]
    assert len(halves)==2
    polys=[footprint(b,cfg["geometry"]["brick_size"]) for b in d.bricks]
    low=min(p.bounds[1] for p in polys)
    high=max(p.bounds[3] for p in polys)
    assert high-low==pytest.approx(9*.054*1.33+.054)
    assert drop_test(d.bricks,cfg).passed


def test_half_supply_size_mass_and_location():
    _,cfg=case()
    spec=cfg["geometry"]["brick_types"]["half"]
    with World(cfg,robot=False) as w:
        body=w.spawn_brick("half",brick_type="half")
        assert pb.getBasePositionAndOrientation(body,physicsClientId=w.cid)[0]==pytest.approx(spec["supply"])
        assert pb.getDynamicsInfo(body,-1,physicsClientId=w.cid)[0]==pytest.approx(spec["mass"])
        lo,hi=pb.getAABB(body,physicsClientId=w.cid)
        assert [hi[i]-lo[i] for i in range(3)]==pytest.approx(spec["size"])


def test_omit_and_none_are_explicit_not_false_approval():
    d,cfg=case("none")
    assert not verify(d,cfg).passed
    omitted,_=case("omit")
    assert len(omitted.bricks)==29
    assert all(b.brick_type=="full" for b in omitted.bricks)


def test_inspection_and_dimension_hint(tmp_path):
    d,cfg=case()
    cfg["geometry"]["max_length"]=.6
    report=verify(d,cfg)
    assert not report.passed
    assert "length/height/depth" in report.issues[0].message
    assert repair_hints(d.program,cfg)["max_bricks_per_course_dimensions"]==8
    inspect_views(d,report,cfg,tmp_path)
    assert (tmp_path/"top.png").stat().st_size>1000
    assert (tmp_path/"front.png").stat().st_size>1000
