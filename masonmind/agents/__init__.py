"""Explicit role classes; agents make only task-level decisions."""
from masonmind.agents.base import Agent
from masonmind.schemas import (ClarifierOutput, ArchitectOutput, SequenceStrategy, StructuralReview,
    ReachabilityReview, CoordinatorDecision, SupervisorDecision, CriticReview, MemoryOutput)


class Clarifier(Agent):
    name, schema = "Clarifier", ClarifierOutput


class Architect(Agent):
    name, schema = "Architect", ArchitectOutput


class SequenceStrategist(Agent):
    name, schema = "SequenceStrategist", SequenceStrategy


class StructuralEngineer(Agent):
    name, schema = "StructuralEngineer", StructuralReview


class RobotOperator(Agent):
    name, schema = "RobotOperator", ReachabilityReview


class Coordinator(Agent):
    name, schema = "Coordinator", CoordinatorDecision


class SiteSupervisor(Agent):
    name, schema = "SiteSupervisor", SupervisorDecision


class VLMCritic(Agent):
    name, schema = "VLMCritic", CriticReview


class MemoryKeeper(Agent):
    name, schema = "MemoryKeeper", MemoryOutput
