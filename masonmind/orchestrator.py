"""Bounded generate/verify/repair orchestration, with no agent motion commands."""
from pathlib import Path
from collections.abc import Callable
from masonmind.config import load_config
from masonmind.io import create_run, save_json
from masonmind.llm.providers import make_client
from masonmind import agents
from masonmind.schemas import Design, AssemblySequence
from masonmind.design.compiler import compile_program
from masonmind.design.verifier import verify
from masonmind.design.sequencer import plan_sequence
from masonmind.design.ifc_export import export_ifc
from masonmind.sim.stability import drop_test


class Orchestrator:
    """The only layer joining agent decisions, geometry, motion and perception."""

    def __init__(self, config: dict | None = None, llm_config: dict | None = None,
                 run: Path | None = None, client=None, ask_user: Callable[[str], str] | None = None):
        self.config, self.llm_config = config or load_config(), llm_config or load_config("llm")
        self.run = run or create_run()
        self.run.mkdir(parents=True, exist_ok=True)
        self.client = client or make_client(self.llm_config)
        self.ask_user = ask_user
        self.repair_rounds = 0
        self.brief = ""
        self.memory_rules: list = []
        save_json(self.run / "config.json", {"physics": self.config, "llm": self.llm_config})

    def agent(self, role: type[agents.Agent]) -> agents.Agent:
        """Instantiate a role with this run's logging and validation settings."""
        return role(self.client, self.llm_config, self.run, self.config["agents"]["validation_retries"])

    def reachability(self, design: Design) -> dict:
        """Check target IK and static empty-scene approach planning, reporting scope."""
        from masonmind.sim.env import World
        from masonmind.sim.robot import Panda
        from masonmind.planning.collision import CollisionChecker
        from masonmind.planning.controller import StaticController
        failures = []
        with World(self.config) as world:
            arm = Panda(world)
            planner = StaticController(CollisionChecker(arm, [world.table]))
            for brick in design.bricks:
                if brick.is_opening:
                    continue
                try:
                    arm.ik([brick.x, brick.y, brick.z + world.sim["grasp_offset"]], brick.yaw_deg)
                    goal = arm.ik([brick.x, brick.y, brick.z + world.sim["grasp_offset"] + world.sim["approach_height"]], brick.yaw_deg)
                    planner.plan(arm.state(), goal)
                except RuntimeError as error:
                    failures.append({"brick_id": brick.id, "error": str(error)})
        return {"passed": not failures, "failures": failures,
                "scope": "Target IK and empty-scene approach plans. Actual carried-brick paths are checked during construction."}

    def run_phase_a(self, user_text: str) -> tuple[Design, AssemblySequence]:
        """Clarify, generate, verify, repair, sequence, review and export a design."""
        clarification = self.agent(agents.Clarifier).decide({"user_text": user_text})
        self.brief = clarification.brief
        if clarification.questions:
            if self.ask_user is None:
                save_json(self.run / "questions.json", clarification)
                raise RuntimeError("Clarification required: " + "; ".join(clarification.questions))
            self.brief += "\n" + "\n".join(self.ask_user(q) for q in clarification.questions)
        feedback = []
        for round_index in range(self.config["agents"]["max_rounds"]):
            program = self.agent(agents.Architect).decide({"brief": self.brief, "feedback": feedback,
                "seed": self.config["seed"], "geometry": self.config["geometry"], "memory_rules": self.memory_rules}).program
            design = compile_program(program, self.config)
            design.version = round_index + 1
            report = verify(design, self.config)
            save_json(self.run / f"program_v{design.version}.json", program)
            save_json(self.run / f"design_v{design.version}.json", design)
            save_json(self.run / f"verification_v{design.version}.json", report)
            if not report.passed:
                feedback = [i.model_dump() for i in report.issues]
                self.repair_rounds += 1
                continue
            strategy = self.agent(agents.SequenceStrategist).decide({"program": program, "issues": feedback, "memory_rules": self.memory_rules})
            sequence = plan_sequence(design, strategy, self.config)
            save_json(self.run / f"sequence_v{design.version}.json", sequence)
            if not sequence.report.passed:
                feedback = [i.model_dump() for i in sequence.report.issues]
                self.repair_rounds += 1
                continue
            stability = drop_test(design.bricks, self.config)
            reachability = self.reachability(design)
            structural = self.agent(agents.StructuralEngineer).decide({"program": program, "verifier": report,
                "drop_test": stability, "passed": report.passed and stability.passed, "memory_rules": self.memory_rules})
            operator = self.agent(agents.RobotOperator).decide({"program": program, **reachability})
            reviews = [structural, operator]
            decision = self.agent(agents.Coordinator).decide({"reviews": reviews})
            save_json(self.run / f"reviews_v{design.version}.json", {"structural": structural, "operator": operator,
                "stability": stability, "reachability": reachability, "coordinator": decision})
            if decision.approved and all(r.approved for r in reviews) and stability.passed and reachability["passed"]:
                export_ifc(design, sequence, self.run / f"design_v{design.version}.ifc", self.config)
                save_json(self.run / "phase_a.json", {"approved": True, "repair_rounds": self.repair_rounds, "brief": self.brief})
                return design, sequence
            feedback = decision.feedback + structural.requested_changes + operator.requested_changes
            self.repair_rounds += 1
        raise RuntimeError(f"Design was not approved after {self.config['agents']['max_rounds']} rounds; last feedback: {feedback}")
