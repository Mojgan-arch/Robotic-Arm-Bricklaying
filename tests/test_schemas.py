import json
import pytest
from pydantic import ValidationError
from masonmind.schemas import DesignProgram, SupervisorDecision, Region, ArchitectOutput
from masonmind.llm.mock import MockClient
from masonmind.schemas import (ClarifierOutput, SequenceStrategy, StructuralReview,
    ReachabilityReview, CoordinatorDecision, CriticReview, MemoryOutput)


def test_program_rejects_coordinates():
    with pytest.raises(ValidationError):
        DesignProgram(n_courses=3, bricks_per_course=4, bond="stack", seed=1, rationale="x", x=1)


def test_region_and_action_validation():
    with pytest.raises(ValidationError):
        Region(course_start=2, course_end=1)
    with pytest.raises(ValidationError):
        SupervisorDecision(action="WAIT", reasoning="blocked")


def test_mock_design_is_reproducible():
    mock = MockClient()
    args = ("Agent: Architect", [{"role": "user", "content": json.dumps({"seed": 8})}])
    assert mock.complete(*args) == mock.complete(*args)
    assert ArchitectOutput.model_validate_json(mock.complete(*args)).program.seed == 8


@pytest.mark.parametrize("agent,schema,data", [
    ("Clarifier", ClarifierOutput, {"user_text": "wall"}),
    ("SequenceStrategist", SequenceStrategy, {}),
    ("StructuralEngineer", StructuralReview, {"passed": True}),
    ("RobotOperator", ReachabilityReview, {"passed": False}),
    ("Coordinator", CoordinatorDecision, {"reviews": [{"approved": True}]}),
    ("SiteSupervisor", SupervisorDecision, {"blocked": True}),
    ("VLMCritic", CriticReview, {}),
    ("MemoryKeeper", MemoryOutput, {}),
])
def test_all_mock_roles(agent, schema, data):
    response = MockClient().complete(f"Agent: {agent}", [{"role": "user", "content": json.dumps(data)}])
    schema.model_validate_json(response)
