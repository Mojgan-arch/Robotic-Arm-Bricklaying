import json
import pytest
from masonmind.config import load_config
from masonmind.schemas import DesignProgram, Design, Brick
from masonmind.design.compiler import compile_program
from masonmind.design.verifier import verify
from masonmind.design.feedback import signature, repair_hints
from masonmind.llm.mock import MockClient
from masonmind.orchestrator import Orchestrator


def bridge(left, right):
    cfg = load_config()
    length, _, height = cfg["geometry"]["brick_size"]
    ox, oy, oz = cfg["geometry"]["origin"]
    bricks = [Brick(id="upper", course=1, index=0, x=ox, y=oy, z=oz+1.5*height, yaw_deg=0)]
    for index, (fraction, sign) in enumerate([(left, -1), (right, 1)]):
        if fraction:
            bricks.append(Brick(id=f"lower{index}", course=0, index=index,
                x=ox+sign*(1-fraction)*length, y=oy, z=oz+.5*height, yaw_deg=0))
    p = DesignProgram(n_courses=3, bricks_per_course=4, bond="running", seed=7, rationale="test")
    return Design(program=p, bricks=bricks), cfg


@pytest.mark.parametrize("left,right,passed", [(.3,.3,True),(.2,.2,True),(.3,0,False),(.1,.3,False),(.16,.16,False)])
def test_bridge_support_rules(left, right, passed):
    design,cfg = bridge(left,right)
    report = verify(design,cfg)
    assert report.passed == passed, report
    if right == 0:
        # 60% one-sided contact contains COM, so use 30% to test COM rejection separately.
        design,cfg = bridge(.3,0)
        assert any(i.check == "com" for i in verify(design,cfg).issues)


def test_positive_com_margin_erodes_hull():
    design,cfg = bridge(.3,.3)
    cfg["geometry"]["com_margin_m"] = .02
    assert any(i.check == "com" for i in verify(design,cfg).issues)


def test_gap_and_radius_contract():
    p = DesignProgram(n_courses=3,bricks_per_course=4,bond="running",seed=7,rationale="test",gap_ratio=.33,plan_curvature=1)
    d=compile_program(p)
    assert d.bricks[1].yaw_deg-d.bricks[0].yaw_deg == pytest.approx(.054*1.33*180/3.141592653589793)
    with pytest.raises(ValueError):
        DesignProgram.model_validate({**p.model_dump(),"arc_angle_deg":20})
    assert signature(p)==signature(p.model_copy(update={"gap_ratio":.33000001,"rationale":"new words"}))


def test_hints_report_no_feasible_geometry():
    p=DesignProgram(n_courses=3,bricks_per_course=4,bond="running",seed=7,rationale="test",gap_ratio=.33,required_supports=2)
    hints=repair_hints(p,load_config())
    assert hints["max_gap_ratio_zero_rotation"] is None  # unsupported end bricks
    assert hints["max_rotation_deg_current_gap"] is None


def test_hints_return_feasible_bounds_for_supported_wall():
    p=DesignProgram(n_courses=3,bricks_per_course=4,bond="stack",seed=7,rationale="test",gap_ratio=.33,
        rotation_rules=[{"region":{"course_start":0,"course_end":2},"kind":"constant","amplitude_deg":20}])
    cfg=load_config()
    hints=repair_hints(p,cfg)
    assert hints["max_gap_ratio_zero_rotation"] == 2
    assert hints["max_rotation_deg_current_gap"] == 45


def test_no_progress_stops_and_identifies_mock(tmp_path, monkeypatch):
    seen=[]
    class Stuck(MockClient):
        def complete(self, system, messages, images=None, json_schema=None):
            if system.startswith("Agent: Architect"):
                seen.append(json.loads(messages[0]["content"])["feedback"])
                p=DesignProgram(n_courses=3,bricks_per_course=4,bond="running",gap_ratio=1,seed=7,rationale=str(len(seen)))
                return json.dumps({"program":p.model_dump()})
            return super().complete(system,messages,images,json_schema)
    cfg=load_config()
    cfg["memory"]["enabled"]=False
    monkeypatch.setattr("masonmind.orchestrator.repair_hints",lambda *args: {})
    owner=Orchestrator(cfg,{**load_config("llm"),"cache":False},tmp_path,client=Stuck())
    with pytest.raises(RuntimeError,match="MockClient"):
        owner.run_phase_a("test")
    assert len(seen)==3
    assert any("MUST change" in str(item) for item in seen[-1])
    assert (tmp_path/"verification_v2.json").exists()
