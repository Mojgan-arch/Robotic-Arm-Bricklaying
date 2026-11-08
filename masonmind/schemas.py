"""Strict public contracts; language models describe rules, never poses."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Region(Schema):
    course_start: int = Field(ge=0)
    course_end: int = Field(ge=0)
    u_start: float = Field(default=0, ge=0, le=1)
    u_end: float = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self) -> "Region":
        if self.course_end < self.course_start or self.u_end <= self.u_start:
            raise ValueError("Region bounds must be ordered and u interval nonempty")
        return self


class RotationRule(Schema):
    region: Region
    kind: Literal["wave", "zigzag", "twist_gradient", "length_gradient", "alternate", "constant"]
    amplitude_deg: float = Field(ge=0, le=45)
    start_deg: float = Field(default=0, ge=-45, le=45)
    frequency: float = Field(default=1, gt=0)
    phase_shift_per_course: float = 0
    combine: Literal["add", "override"] = "add"


class OpeningRule(Schema):
    region: Region
    kind: Literal["gradient", "diamond", "stripe", "checker", "random", "hit_and_miss"]
    ratio_start: float = Field(default=0, ge=0, le=0.5)
    ratio_end: float = Field(default=0, ge=0, le=0.5)
    size: float | None = Field(default=None, gt=0, le=1)


class DesignProgram(Schema):
    n_courses: int = Field(ge=3, le=15)
    bricks_per_course: int = Field(ge=4, le=14)
    bond: Literal["running", "stack"]
    end_treatment: Literal["half_brick", "omit", "none"] = "half_brick"
    gap_ratio: float | None = Field(default=None, ge=0, le=2)
    plan_curvature: float | None = Field(default=None, gt=0, description="Circular centreline radius in metres; None uses legacy arc_angle_deg")
    arc_angle_deg: float = Field(default=0, ge=-90, le=90, description="Signed centreline sweep; zero is straight")
    required_supports: int = Field(default=1, ge=1, le=2, description="Minimum distinct supporting bricks above the base course")
    rotation_rules: list[RotationRule] = Field(default_factory=list)
    opening_rules: list[OpeningRule] = Field(default_factory=list)
    seed: int
    rationale: str

    @model_validator(mode="after")
    def regions_inside(self) -> "DesignProgram":
        if self.plan_curvature is not None and self.arc_angle_deg != 0:
            raise ValueError("Use plan_curvature or arc_angle_deg, not both")
        for rule in [*self.rotation_rules, *self.opening_rules]:
            if rule.region.course_end >= self.n_courses:
                raise ValueError("Rule region exceeds n_courses")
        return self


class Brick(Schema):
    brick_type: Literal["full", "half"] = "full"
    size: list[float] | None = None
    id: str
    course: int = Field(ge=0)
    index: int = Field(ge=0)
    x: float
    y: float
    z: float
    yaw_deg: float
    is_opening: bool = False


class Design(Schema):
    program: DesignProgram
    bricks: list[Brick]
    version: int = Field(default=1, ge=1)


class VerificationIssue(Schema):
    check: Literal["dimensions", "overlap", "support", "com", "sequence_precedence", "intermediate_stability", "gripper_clearance", "reachability"]
    brick_ids: list[str]
    message: str
    value: float | None = None
    limit: float | None = None


class VerificationReport(Schema):
    passed: bool
    issues: list[VerificationIssue] = Field(default_factory=list)


class SequenceStrategy(Schema):
    strategy: Literal["left_to_right", "right_to_left", "center_out", "ends_in", "high_rotation_first", "low_rotation_first"]
    rationale: str


class AssemblySequence(Schema):
    order: list[str]
    strategy: SequenceStrategy
    report: VerificationReport


class BrickDeviation(Schema):
    brick_id: str
    dx_mm: float
    dy_mm: float
    dz_mm: float
    dyaw_deg: float
    missing: bool
    within_tolerance: bool


class MotionEvent(Schema):
    t: float
    kind: Literal["slowdown", "stop", "replan_ok", "replan_failed", "resume"]
    min_distance_m: float
    detail: str


class SupervisorDecision(Schema):
    action: Literal["ACCEPT", "REPOSITION", "REPLACE", "REPLAN_REMAINING", "RESEQUENCE", "WAIT", "ASK_USER"]
    brick_ids: list[str] = Field(default_factory=list)
    new_program_for_remaining: DesignProgram | None = None
    new_strategy: SequenceStrategy | None = None
    wait_seconds: float | None = Field(default=None, gt=0, le=300)
    question_for_user: str | None = None
    reasoning: str

    @model_validator(mode="after")
    def action_arguments(self) -> "SupervisorDecision":
        required = {"REPOSITION": self.brick_ids, "REPLACE": self.brick_ids,
                    "REPLAN_REMAINING": self.new_program_for_remaining,
                    "RESEQUENCE": self.new_strategy, "WAIT": self.wait_seconds,
                    "ASK_USER": self.question_for_user}
        if self.action in required and not required[self.action]:
            raise ValueError(f"{self.action} requires its action-specific argument")
        return self


class ClarifierOutput(Schema):
    questions: list[str] = Field(default_factory=list)
    brief: str


class ArchitectOutput(Schema):
    program: DesignProgram


class StructuralReview(Schema):
    approved: bool
    requested_changes: list[str] = Field(default_factory=list)
    reasoning: str


class ReachabilityReview(StructuralReview):
    pass


class CoordinatorDecision(Schema):
    approved: bool
    feedback: list[str] = Field(default_factory=list)


class CriticReview(Schema):
    intent_match_score: float = Field(ge=0, le=10)
    issues: list[str] = Field(default_factory=list)


class MemoryRule(Schema):
    text: str
    run_id: str
    course: int
    brick_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    times_confirmed: int = Field(default=1, ge=1)


class MemoryOutput(Schema):
    rules: list[MemoryRule] = Field(default_factory=list)


class StabilityReport(Schema):
    passed: bool
    max_translation_m: float
    max_angle_deg: float
    unstable_ids: list[str] = Field(default_factory=list)


class BuildReport(Schema):
    completed: bool
    run_id: str
    placed: int
    total: int
    duration_s: float
    collisions: int = 0
    metrics: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
