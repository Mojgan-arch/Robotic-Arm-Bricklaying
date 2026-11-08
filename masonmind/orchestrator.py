"""Bounded generate/verify/repair orchestration, with no agent motion commands."""
from pathlib import Path
from collections.abc import Callable
import numpy as np
from masonmind.config import load_config
from masonmind.io import create_run, save_json
from masonmind.llm.providers import make_client
from masonmind.llm import LLMClient
from masonmind.scan.registration import ScanResult
from masonmind import agents
from masonmind.schemas import (Design, AssemblySequence, BuildReport, SupervisorDecision,
    DesignProgram, SequenceStrategy, StructuralReview, ReachabilityReview, CoordinatorDecision, ClarifierOutput, Brick)
from masonmind.design.compiler import compile_program
from masonmind.design.verifier import verify
from masonmind.design.sequencer import plan_sequence
from masonmind.design.ifc_export import export_ifc
from masonmind.sim.stability import drop_test
from masonmind.interaction import ClarificationRequired
from masonmind.design.feedback import signature, compact_issues, repair_hints


class Orchestrator:
    """The only layer joining agent decisions, geometry, motion and perception."""

    def __init__(self, config: dict | None = None, llm_config: dict | None = None,
                 run: Path | None = None, client: LLMClient | None = None, ask_user: Callable[[str], str] | None = None,
                 observer: Callable[[dict], None] | None = None) -> None:
        self.config, self.llm_config = config or load_config(), llm_config or load_config("llm")
        if self.config["memory"]["path"] == "memory/rules.jsonl":
            self.config["memory"]["path"] = f"memory/{self.llm_config['provider']}_rules.jsonl"
        if self.config.get("variant", "B3") != "B3":
            self.config["memory"]["enabled"] = False
        self.run = run or create_run()
        self.run.mkdir(parents=True, exist_ok=True)
        self.client = client or make_client(self.llm_config)
        self.ask_user = ask_user
        self.observer = observer
        self.repair_rounds = 0
        self.brief = ""
        self.memory_rules: list = []
        from masonmind.memory import MemoryStore
        self.memory = MemoryStore(self.config["memory"])
        save_json(self.run / "config.json", {"physics": self.config, "llm": self.llm_config})

    def agent(self, role: type[agents.Agent]) -> agents.Agent:
        """Instantiate a role with this run's logging and validation settings."""
        if self.config.get("variant") == "B1":
            role = {agents.Architect: agents.SingleDesigner, agents.SiteSupervisor: agents.SingleSupervisor}.get(role, role)
        return role(self.client, self.llm_config, self.run, self.config["agents"]["validation_retries"], self.emit)

    def emit(self, kind: str, **evidence: object) -> None:
        """Publish auditable progress; camera pixels are display-only, never agent truth."""
        from masonmind.io import append_json
        event = {"kind": kind, **evidence}
        if kind != "frame":
            append_json(self.run / "timeline.jsonl", event)
        if self.observer:
            self.observer(event)

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
        variant = self.config.get("variant", "B3")
        if variant == "B0":
            self.brief = user_text
            program = DesignProgram.model_validate({**self.config["baseline"], "seed": self.config["seed"]})
            design = compile_program(program, self.config)
            sequence = plan_sequence(design, SequenceStrategy(strategy="left_to_right", rationale="Fixed B0 order"), self.config)
            report = verify(design, self.config)
            if not report.passed or not sequence.report.passed:
                raise RuntimeError("Fixed baseline design/sequence failed validation")
            save_json(self.run / "design_v1.json", design)
            save_json(self.run / "program_v1.json", program)
            save_json(self.run / "verification_v1.json", report)
            save_json(self.run / "sequence_v1.json", sequence)
            export_ifc(design, sequence, self.run / "design_v1.ifc", self.config)
            return design, sequence
        clarification = (ClarifierOutput(brief=user_text) if variant == "B1" else
                         self.agent(agents.Clarifier).decide({"user_text": user_text,
                             "geometry": self.config["geometry"], "design_defaults": self.config.get("design_defaults", {}),
                             "environment": "Small-scale simulation with known brick dimensions; not a load-bearing building."}))
        self.brief = clarification.brief
        self.memory_rules = self.memory.retrieve(self.brief)
        if clarification.questions:
            if self.ask_user is None:
                save_json(self.run / "questions.json", clarification)
                raise ClarificationRequired(clarification.questions)
            self.brief += "\n" + "\n".join(self.ask_user(q) for q in clarification.questions)
        feedback = []
        previous, repeated = None, 0
        main_checks = "review"
        for round_index in range(self.config["agents"]["max_rounds"]):
            program = self.agent(agents.Architect).decide({"brief": self.brief, "original_user_text": user_text, "feedback": feedback,
                "seed": self.config["seed"], "geometry": self.config["geometry"],
                "design_defaults": self.config.get("design_defaults", {}), "memory_rules": self.memory_rules}).program
            current = signature(program)
            repeated = repeated + 1 if current == previous else 0
            previous = current
            if repeated >= 2:
                save_json(self.run / "failure.json", {"reason": "no_progress", "feedback": feedback, "program": program})
                mode = " MockClient is active; it does not interpret repair requests." if self.llm_config["provider"] == "mock" else ""
                raise RuntimeError(f"No progress: two unchanged repair rounds; {main_checks} still fails. Change gap, rotation, openings or edge supports." + mode + " Full details are in the run log.")
            design = compile_program(program, self.config)
            design.version = round_index + 1
            report = verify(design, self.config)
            save_json(self.run / f"program_v{design.version}.json", program)
            save_json(self.run / f"design_v{design.version}.json", design)
            save_json(self.run / f"verification_v{design.version}.json", report)
            self.emit("verification", version=design.version, passed=report.passed, summary=compact_issues(design, report))
            if not report.passed:
                from masonmind.design.inspection import inspect_views
                inspect_views(design, report, self.config, self.run / f"inspection_v{design.version}")
                main_checks = ", ".join(sorted({i.check for i in report.issues}))
                hints = repair_hints(program, self.config)
                feedback = compact_issues(design, report) + [hints]
                if repeated:
                    feedback.append("NO PROGRESS: your design is unchanged. You MUST change geometric parameters; changing rationale is not a repair. Another identical round will stop.")
                save_json(self.run / f"feedback_v{design.version}.json", feedback)
                self.repair_rounds += 1
                continue
            strategy = (SequenceStrategy(strategy="left_to_right", rationale="Single-agent baseline fixed order") if variant == "B1" else
                        self.agent(agents.SequenceStrategist).decide({"program": program, "issues": feedback, "memory_rules": self.memory_rules}))
            self.emit("design_stage", message="Checking placement order and intermediate physical stability")
            sequence = plan_sequence(design, strategy, self.config)
            save_json(self.run / f"sequence_v{design.version}.json", sequence)
            if not sequence.report.passed:
                from masonmind.design.inspection import inspect_views
                inspect_views(design, sequence.report, self.config, self.run / f"inspection_v{design.version}")
                main_checks = ", ".join(sorted({i.check for i in sequence.report.issues}))
                feedback = compact_issues(design, sequence.report)
                if repeated:
                    feedback.append("NO PROGRESS: change the geometric design; another identical round will stop.")
                self.repair_rounds += 1
                continue
            self.emit("design_stage", message="Checking whole-wall physical stability and robot reachability")
            stability = drop_test(design.bricks, self.config)
            reachability = self.reachability(design)
            structural = (StructuralReview(approved=stability.passed, reasoning="Deterministic baseline guard") if variant == "B1" else
                          self.agent(agents.StructuralEngineer).decide({"brief": self.brief, "program": program, "verifier": report,
                "stability_limits": {"translation_m": self.config["sim"]["stability_translation_m"],
                                     "angle_deg": self.config["sim"]["stability_angle_deg"]},
                "drop_test": stability, "passed": report.passed and stability.passed, "memory_rules": self.memory_rules}))
            operator = (ReachabilityReview(approved=reachability["passed"], reasoning="Deterministic baseline guard") if variant == "B1" else
                        self.agent(agents.RobotOperator).decide({"program": program, **reachability}))
            reviews = [structural, operator]
            decision = (CoordinatorDecision(approved=all(r.approved for r in reviews)) if variant == "B1" else
                        self.agent(agents.Coordinator).decide({"reviews": reviews}))
            save_json(self.run / f"reviews_v{design.version}.json", {"structural": structural, "operator": operator,
                "stability": stability, "reachability": reachability, "coordinator": decision})
            if decision.approved and all(r.approved for r in reviews) and stability.passed and reachability["passed"]:
                export_ifc(design, sequence, self.run / f"design_v{design.version}.ifc", self.config)
                save_json(self.run / "phase_a.json", {"approved": True, "repair_rounds": self.repair_rounds, "brief": self.brief})
                self.emit("design_approved", bricks=len(sequence.order), repair_rounds=self.repair_rounds)
                return design, sequence
            feedback = decision.feedback + structural.requested_changes + operator.requested_changes
            from masonmind.design.inspection import inspect_views
            from masonmind.schemas import VerificationReport, VerificationIssue
            review_failure = VerificationReport(passed=False, issues=[VerificationIssue(
                check="intermediate_stability" if not stability.passed else "reachability",
                brick_ids=stability.unstable_ids + [f["brick_id"] for f in reachability["failures"]],
                message="Design review was not approved; see reviews JSON for supplied evidence.")])
            inspect_views(design, review_failure, self.config, self.run / f"inspection_v{design.version}")
            if repeated:
                feedback.append("NO PROGRESS: change the design; another identical round will stop.")
            self.repair_rounds += 1
        save_json(self.run / "failure.json", {"reason": "repair_budget", "feedback": feedback})
        mode = " MockClient is active (fixed offline fixture)." if self.llm_config["provider"] == "mock" else ""
        raise RuntimeError(f"Design remains unapproved: {main_checks}. Reduce gaps/rotation or add edge supports; full details are in the run log." + mode)

    def run_phase_b(self, design: Design, sequence: AssemblySequence, scenario: str = "clean",
                    variant: str = "B3", video: bool = True) -> BuildReport:
        """Build with sensing, bounded interventions and full artifact logging."""
        from masonmind.design.sequencer import dependencies
        expected = {b.id for b in design.bricks if not b.is_opening}
        if set(sequence.order) != expected or len(sequence.order) != len(expected):
            raise ValueError("Assembly sequence must contain every solid brick exactly once")
        if not sequence.report.passed or not verify(design, self.config).passed:
            raise ValueError("Construction requires a verified design and sequence")
        rank = {name: index for index, name in enumerate(sequence.order)}
        if any(rank[parent] >= rank[name] for name, parents in dependencies(design, self.config).items() for parent in parents):
            raise ValueError("Assembly sequence violates supporting precedence")
        session = BuildSession(self, design, sequence, scenario, variant, video)
        return session.run_build()


class BuildSession:
    """Construction state machine owned by the orchestrator.

    Simulation owns disturbance truth; sensors produce all deviation evidence.
    Collision proxies use nominal poses until observed poses are available.
    """

    def __init__(self, owner: Orchestrator, design: Design, sequence: AssemblySequence,
                 scenario: str, variant: str, video: bool) -> None:
        self.owner, self.design, self.sequence = owner, design, sequence
        self.config, self.run = owner.config, owner.run
        self.scenario = load_config(f"scenarios/{scenario}")
        self.scenario_name, self.variant, self.video = scenario, variant, video
        self.done: list[str] = []
        self.remaining = list(sequence.order)
        self.stock = self.config["construction"]["initial_stock"]
        self.interventions: dict[str, int] = {}
        self.latest_scan = None
        self.scan_index = 0
        self.decisions = []
        self.incidents: list[dict] = []
        self.handled_course_events: set[tuple] = set()
        self.collisions = 0
        self.failure: str | None = None
        self.critic_scores: list[float] = []
        self.memory_index = 0

    def observe(self) -> list[int]:
        """Detect unknown objects from depth and update query-only geometry."""
        from masonmind.planning.perception import detect_obstacles
        frames = [camera.capture(include_rgb=False) for camera in self.monitor_cameras]
        boxes = detect_obstacles(frames, self.world.known_ids, self.config["scan"])
        self.proxies.update_obstacles(boxes)
        self.checker.scene = [self.world.table, *self.proxies.bricks.values(), *self.proxies.obstacles]
        self.checker.support_bodies = {self.world.table, *self.proxies.bricks.values()}
        return self.proxies.obstacles

    def refresh_known(self) -> None:
        """Refresh known collision IDs after a nominal or scanned pose changes."""
        self.checker.scene = [self.world.table, *self.proxies.bricks.values(), *self.proxies.obstacles]
        self.checker.support_bodies = {self.world.table, *self.proxies.bricks.values()}

    def scan(self, bricks: list[Brick]) -> ScanResult:
        """Store estimates and independent truth error in separate artifacts."""
        from masonmind.scan.registration import scan_bricks
        from masonmind.scan.evaluation import evaluate_scan
        from masonmind.scan.heatmap import heatmap
        frames = [camera.capture() for camera in self.cameras]
        result = scan_bricks(frames, bricks, self.world.bricks, self.config)
        folder = self.run / "scans" / f"scan_{self.scan_index:03}"
        self.scan_index += 1
        save_json(folder / "deviations.json", result.deviations)
        save_json(folder / "diagnostics.json", result.diagnostics)
        save_json(folder / "accuracy.json", evaluate_scan(bricks, result.deviations, self.world.bricks, self.world.cid))
        heatmap(result.deviations, folder / "heatmap.png", self.config["scan"]["translation_tolerance_mm"])
        from PIL import Image
        Image.fromarray(frames[0].rgb).save(folder / "rgb.png")
        import numpy as np
        np.savez_compressed(folder / "points.npz", **{f"view_{i}": f.points for i, f in enumerate(frames)},
                            **{f"ids_{i}": f.body_ids for i, f in enumerate(frames)})
        self.latest_scan, self.latest_scan_folder = result, folder
        self.owner.emit("scan", deviations=result.deviations, heatmap=str((folder / "heatmap.png").resolve()),
                        broken_ids=result.broken_ids)
        if self.variant != "B0":
            for name, (position, yaw) in result.poses.items():
                self.proxies.update_brick(name, position, yaw, next(b.size for b in bricks if b.id == name))
            self.refresh_known()
        return result

    def supervisor(self, **evidence: object) -> SupervisorDecision:
        """Invoke a validated decision with measured evidence and current stock."""
        course = evidence.pop("course", -1)
        measurements = evidence.get("deviations")
        if measurements is not None:
            evidence["scan_summary"] = {"total": len(measurements),
                "within_tolerance": sum(d.within_tolerance for d in measurements),
                "unobserved": sum(d.missing for d in measurements)}
            evidence["deviations"] = [d for d in measurements if not d.within_tolerance or d.missing]
            if evidence["deviations"] or evidence.get("broken_ids"):
                # Reject invalid acceptance inside the agent's bounded repair
                # loop, before it becomes an executable decision. The execution
                # guard remains as independent defense in depth.
                permitted = evidence.get("allowed_actions", ["ACCEPT", "REPOSITION", "REPLACE",
                    "REPLAN_REMAINING", "RESEQUENCE", "WAIT", "ASK_USER"])
                evidence["allowed_actions"] = [action for action in permitted if action != "ACCEPT"]
        decision = self.owner.agent(agents.SiteSupervisor).decide({
            "allowed_brick_ids": list(self.done),
            "course": course, "remaining_program": self.design.program,
            "supply_count": self.stock, "memory_rules": self.owner.memory_rules,
            "motion_events": [] if self.controller is None else self.controller.events[-8:], **evidence})
        from masonmind.io import append_json
        append_json(self.run / "decisions.jsonl", decision)
        self.decisions.append(decision)
        self.interventions[decision.action] = self.interventions.get(decision.action, 0) + 1
        if decision.action != "ACCEPT":
            self.incidents.append({"course": course, "action": decision.action, "brick_ids": decision.brick_ids})
        return decision

    def supervised_plan(self, start: np.ndarray, goal: np.ndarray) -> list[np.ndarray]:
        """Retry the same task after a concrete supervisor intervention."""
        from masonmind.planning.rrt_connect import PlanningError
        for _ in range(self.config["construction"]["max_motion_retries"]):
            try:
                return self.controller.plan(start, goal)
            except PlanningError as error:
                from masonmind.io import append_json
                append_json(self.run / "planning_failures.jsonl", {"phase": self.arm.phase, "error": str(error)})
                decision = self.supervisor(blocked=True, planning_error=str(error))
                if decision.action not in {"WAIT", "ASK_USER"}:
                    raise RuntimeError("An in-progress grasp requires WAIT/ASK_USER before changing tasks")
                self.apply_decision(decision)
        raise PlanningError("Supervisor motion retry budget exhausted")

    def supervised_execute(self, path: list[np.ndarray]) -> None:
        """Resume an interrupted primitive from current encoders, not from its start."""
        from masonmind.planning.rrt_connect import PlanningError
        goal = path[-1]
        for attempt in range(self.config["construction"]["max_motion_retries"]):
            try:
                before = len(self.controller.events)
                self.controller.execute(path)
                recovered = self.controller.events[before:]
                if any(event.kind == "stop" for event in recovered):
                    decision = self.supervisor(motion_recovered=True, blocked=False,
                                               allowed_actions=["ACCEPT", "WAIT", "ASK_USER"])
                    if decision.action not in {"ACCEPT", "WAIT", "ASK_USER"}:
                        raise RuntimeError("Motion recovery review cannot change an in-progress grasp")
                    self.apply_decision(decision)
                return
            except PlanningError:
                decision = self.supervisor(blocked=True)
                self.apply_decision(decision)
                path = self.supervised_plan(self.arm.state(), goal)
        raise PlanningError("Supervisor execution retry budget exhausted")

    def place(self, brick: Brick, corrective: bool = False) -> None:
        """Consume stock and physically place it; correction runs use fresh calibrated stock."""
        if self.stock <= 0:
            raise RuntimeError("No stock available")
        self.stock -= 1
        body = self.world.spawn_brick(brick.id, length_scale=1 if corrective else self.disturbances.stock_length_scale(), brick_type=brick.brick_type)
        self.world.step(self.config["sim"]["settle_steps"])
        self.arm.release_hook = None if corrective else self.disturbances.on_release
        self.arm.drop_early = False if corrective else self.disturbances.should_drop()
        supply = self.config["geometry"]["brick_types"].get(brick.brick_type, {}).get("supply", self.config["sim"]["supply"])
        self.arm.pick_place(body, supply, [brick.x, brick.y, brick.z], brick.yaw_deg)
        self.arm.release_hook = None
        self.proxies.update_brick(brick.id, [brick.x, brick.y, brick.z], brick.yaw_deg, brick.size)
        self.refresh_known()

    def apply_decision(self, decision: SupervisorDecision) -> None:
        """Execute all seven supported actions; invalid/unsafe requests fail explicitly."""
        from masonmind.design.sequencer import dependencies, gripper_collisions
        from masonmind.schemas import DesignProgram
        from masonmind.io import append_json
        import json
        if decision.action == "ACCEPT":
            return
        if decision.action == "WAIT":
            self.arm.command(self.arm.state())
            self.world.step(round(decision.wait_seconds / self.config["sim"]["dt"]))
            if self.variant != "B0":
                self.observe()
            return
        if decision.action == "ASK_USER":
            if self.owner.ask_user:
                answer = self.owner.ask_user(decision.question_for_user)
            else:
                event = "shortage" if self.stock <= 0 else "user_change"
                answer = self.config["construction"]["batch_answers"][event]
            append_json(self.run / "user_answers.jsonl", {"question": decision.question_for_user, "answer": answer, "scripted": self.owner.ask_user is None})
            if answer == "replenish":
                self.stock += self.config["construction"]["initial_stock"]
            elif answer in {"continue", "keep_current_design"}:
                return
            else:
                program = DesignProgram.model_validate(json.loads(answer))
                self.apply_decision(SupervisorDecision(action="REPLAN_REMAINING", new_program_for_remaining=program, reasoning="User supplied a revised DSL"))
            return
        if decision.action in {"RESEQUENCE", "REPLAN_REMAINING"}:
            updated = self.design
            if decision.action == "REPLAN_REMAINING":
                updated = compile_program(decision.new_program_for_remaining, self.config)
                fixed = [b for b in self.design.bricks if b.id in self.done]
                # Completed courses are immutable, including their openings.
                completed_courses = {b.course for b in fixed if not any(x.course == b.course and x.id in self.remaining for x in self.design.bricks)}
                fixed = [b for b in self.design.bricks if b.course in completed_courses or b.id in self.done]
                if any(b.course >= updated.program.n_courses for b in fixed):
                    raise RuntimeError("Revised program would remove a built course")
                fixed_ids = {b.id for b in fixed}
                updated.bricks = fixed + [b for b in updated.bricks if b.course not in completed_courses and b.id not in fixed_ids]
                updated.version = self.design.version + 1
            verification = verify(updated, self.config)
            if not verification.passed or not drop_test(updated.bricks, self.config).passed:
                raise RuntimeError("Supervisor revision failed geometry or stability verification")
            strategy = decision.new_strategy or self.sequence.strategy
            sequence = plan_sequence(updated, strategy, self.config)
            if not sequence.report.passed:
                raise RuntimeError("Supervisor revision failed sequence verification")
            remaining = [name for name in sequence.order if name not in self.done]
            by_id = {b.id: b for b in updated.bricks}
            placed = [by_id[name] for name in self.done]
            for name in remaining:
                if gripper_collisions(by_id[name], placed, self.config):
                    raise RuntimeError("Revised remaining order conflicts with actual placement history")
                placed.append(by_id[name])
            self.design, self.sequence, self.remaining = updated, sequence, remaining
            save_json(self.run / f"revised_design_v{updated.version}.json", updated)
            save_json(self.run / f"revised_sequence_v{updated.version}.json", sequence)
            export_ifc(updated, sequence, self.run / f"design_v{updated.version}.ifc", self.config)
            return
        if self.latest_scan is None:
            raise RuntimeError("Correction requires a measured scan")
        by_id = {b.id: b for b in self.design.bricks}
        graph = dependencies(self.design, self.config)
        for name in decision.brick_ids:
            if name not in self.done or name not in by_id:
                raise RuntimeError(f"Cannot correct an unbuilt/unknown brick: {name}")
            if any(name in graph[other] for other in self.done if other != name):
                raise RuntimeError(f"Cannot extract loaded brick {name}; supporting dependents must be removed first")
            if name not in self.latest_scan.poses:
                raise RuntimeError(f"Cannot safely manipulate unobserved brick {name}; rescan or ask user")
            position, yaw = self.latest_scan.poses[name]
            self.proxies.remove_brick(name)
            self.refresh_known()
            brick = by_id[name]
            self.arm.release_hook = None
            body = self.world.bricks[name]
            if decision.action == "REPOSITION":
                self.arm.pick_place(body, position, [brick.x, brick.y, brick.z], brick.yaw_deg, yaw)
            elif decision.action == "REPLACE":
                self.arm.pick_place(body, position, self.config["sim"]["discard"], source_yaw_deg=yaw)
                # Preserve discarded physical material under a new instance label.
                discarded = self.world.bricks.pop(name)
                self.world.bricks[f"discarded_{name}_{len(self.decisions)}"] = discarded
                self.place(brick, corrective=True)
            else:
                raise RuntimeError(f"Unhandled supervisor action {decision.action}")
            self.proxies.update_brick(name, [brick.x, brick.y, brick.z], brick.yaw_deg, brick.size)
            self.refresh_known()

    def course_events(self, course: int) -> None:
        """Inject stock and user-request events once, never infer them from hidden flags."""
        for kind, setting in (("shortage", "material_shortage_at_course"), ("user_change", "user_change_at_course")):
            if self.scenario.get(setting) != course or (kind, course) in self.handled_course_events:
                continue
            self.handled_course_events.add((kind, course))
            if kind == "shortage":
                self.stock = 0
            if self.variant == "B0":
                if kind == "shortage":
                    raise RuntimeError("Open-loop baseline exhausted stock")
                continue
            evidence = {"course": course, kind: self.scenario.get("user_change_text", True) if kind == "user_change" else True}
            self.apply_decision(self.supervisor(**evidence))

    def after_course(self, course: int) -> None:
        """Measure, require a supervisor decision for failures, and verify corrections."""
        bricks = [b for b in self.design.bricks if b.id in self.done]
        scan = self.scan(bricks)
        if self.variant == "B0":
            return
        for _ in range(self.config["construction"]["max_interventions_per_course"]):
            conforming = all(d.within_tolerance for d in scan.deviations) and not scan.broken_ids
            decision = self.supervisor(course=course, deviations=scan.deviations, broken_ids=scan.broken_ids)
            self.apply_decision(decision)
            if decision.action == "ACCEPT":
                if not conforming:
                    raise RuntimeError("Supervisor ACCEPT cannot override measured out-of-tolerance construction")
                break
            scan = self.scan(bricks)
        else:
            raise RuntimeError(f"Course {course} still outside tolerance after intervention budget")
        self.review_course(course, scan)

    def review_course(self, course: int, scan: ScanResult) -> None:
        """Critique visible intent and retain lessons only with measured outcomes."""
        from masonmind.io import append_json
        folder = self.latest_scan_folder
        critic = self.owner.agent(agents.VLMCritic).decide(
            {"brief": self.owner.brief, "course": course,
             "measured_count": len(scan.deviations),
             "within_tolerance_count": sum(d.within_tolerance for d in scan.deviations),
             "deviations": [d for d in scan.deviations if not d.within_tolerance or d.missing]},
            images=[(folder / "rgb.png").read_bytes(), (folder / "heatmap.png").read_bytes()])
        self.critic_scores.append(critic.intent_match_score)
        append_json(self.run / "critic.jsonl", {"course": course, **critic.model_dump(),
                    "mock_fixture": self.owner.llm_config["provider"] == "mock"})
        if self.variant == "B3" and self.owner.memory.enabled:
            for incident in self.incidents[self.memory_index:]:
                outcome = {"all_observed_within_tolerance": all(d.within_tolerance for d in scan.deviations),
                           "scan": scan.deviations}
                output = self.owner.agent(agents.MemoryKeeper).decide({"incident": incident, "outcome": outcome,
                    "run_id": self.run.name, "course": course, "brick_ids": incident["brick_ids"]})
                for rule in output.rules:
                    # The evidence identifiers come from the run, not model invention.
                    rule.run_id, rule.course = self.run.name, course
                    rule.brick_ids = list(incident["brick_ids"])
                    self.owner.memory.add(rule)
                    self.owner.emit("memory_rule", rule=rule.model_dump(), outcome=outcome)
            self.memory_index = len(self.incidents)
            self.owner.memory_rules = self.owner.memory.retrieve(self.owner.brief)

    def run_build(self) -> BuildReport:
        """Own resources and write a report even if construction cannot complete."""
        import time
        import numpy as np
        import pybullet as p
        from masonmind.sim.env import World
        from masonmind.sim.robot import Panda
        from masonmind.sim.camera import Camera
        from masonmind.sim.recorder import Recorder
        from masonmind.sim.disturbances import Disturbances
        from masonmind.sim.obstacles import ScriptedObstacle
        from masonmind.planning.proxies import ProxyScene
        from masonmind.planning.collision import CollisionChecker
        from masonmind.planning.executor import MonitoredExecutor
        started = time.perf_counter()
        save_json(self.run / "scenario.json", self.scenario)
        with World(self.config) as world:
            self.world, self.arm = world, Panda(world)
            if self.owner.observer and self.config["ui"].get("stream_frames", True):
                display = self.config["ui"]
                live_camera = Camera(world, eye=display["idle_camera_eye"], resolution=tuple(display["live_resolution"]), target=display["idle_camera_target"])
                next_frame = 0.0
                def publish_frame() -> None:
                    nonlocal next_frame
                    now = time.perf_counter()
                    if now >= next_frame:
                        self.owner.emit("frame", image=live_camera.capture(unproject=False).rgb,
                                        phase=self.arm.phase, simulation_time=world.t,
                                        placed=len(self.done), total=len(self.done) + len(self.remaining))
                        next_frame = time.perf_counter() + display["frame_interval_s"]
                world.on_step.append(publish_frame)
            self.cameras = [Camera(world, eye) for eye in self.config["scan"]["camera_eyes"]]
            self.monitor_cameras = [Camera(world, eye, (self.config["scan"]["monitor_width"], self.config["scan"]["monitor_height"]))
                                    for eye in self.config["scan"]["camera_eyes"]]
            self.proxies = ProxyScene(world)
            self.checker = CollisionChecker(self.arm, [world.table])
            self.disturbances = Disturbances(world, self.scenario)
            recorder = Recorder(Camera(world), self.run / "video.mp4") if self.video else None
            obstacle = ScriptedObstacle(world, self.scenario["obstacle"]) if self.scenario["obstacle"] else None
            def audit() -> None:
                if obstacle:
                    bodies = [self.arm.body] + ([] if self.arm.held is None else [self.arm.held])
                    if any(p.getContactPoints(body, obstacle.body, physicsClientId=world.cid) for body in bodies):
                        self.collisions += 1
            world.on_step.append(audit)  # Evaluation only: never feeds a decision.
            self.controller = None
            if self.variant != "B0":
                self.controller = MonitoredExecutor(self.checker, self.observe, self.run / "motion_log.jsonl", recorder)
                self.controller.observer = lambda event: self.owner.emit("motion", event=event.model_dump())
                self.controller.allow_replan = self.variant != "B1"
                self.controller.observe(force=True)
                if self.variant == "B1":
                    from masonmind.planning.controller import StaticController
                    self.arm.plan = self.controller.plan
                    self.arm.execute = lambda path: StaticController.execute(self.controller, path)
                else:
                    self.arm.plan, self.arm.execute = self.supervised_plan, self.supervised_execute
            try:
                while self.remaining:
                    name = self.remaining[0]
                    brick = next(b for b in self.design.bricks if b.id == name)
                    self.course_events(brick.course)
                    self.place(brick)
                    self.done.append(name)
                    self.remaining.remove(name)
                    self.owner.emit("brick_placed", brick=name, course=brick.course, placed=len(self.done),
                                    total=len(self.done) + len(self.remaining))
                    if not any(b.course == brick.course and b.id in self.remaining for b in self.design.bricks):
                        self.after_course(brick.course)
                self.scan([b for b in self.design.bricks if b.id in self.done])
                if self.variant == "B0":
                    # Evaluation-only VLM scoring after the open-loop build is over.
                    self.review_course(-1, self.latest_scan)
            except (RuntimeError, ValueError) as error:
                self.failure = str(error)
                save_json(self.run / "failure.json", {"error": str(error), "phase": self.arm.phase, "remaining": self.remaining})
            finally:
                if recorder:
                    recorder.close()
            # Independent final truth metrics; these do not control the robot.
            errors, yaw_errors, geometry_errors, collapsed = [], [], [], False
            for brick in self.design.bricks:
                if brick.id not in self.done:
                    continue
                position, orientation = p.getBasePositionAndOrientation(world.bricks[brick.id], physicsClientId=world.cid)
                errors.append(float(np.linalg.norm(np.asarray(position) - [brick.x, brick.y, brick.z]) * 1000))
                yaw_errors.append(float(abs((np.rad2deg(p.getEulerFromQuaternion(orientation)[2]) - brick.yaw_deg + 180) % 360 - 180)))
                actual_size = np.asarray(p.getCollisionShapeData(world.bricks[brick.id], -1, physicsClientId=world.cid)[0][3])
                size_error = np.linalg.norm((actual_size - (brick.size or self.config["geometry"]["brick_size"])) / 2) * 1000
                geometry_errors.append(float(np.hypot(errors[-1], size_error)))
                collapsed |= abs(position[2] - brick.z) > self.config["geometry"]["brick_size"][2] / 2
            events = [] if self.controller is None else self.controller.events
            metrics = {"variant": self.variant, "scenario": self.scenario_name, "seed": self.config["seed"],
                "llm_provider": self.owner.llm_config["provider"], "llm_model": self.owner.llm_config["model"],
                "mean_deviation_mm": float(np.mean(errors)) if errors else None, "max_deviation_mm": max(errors, default=None),
                "mean_deviation_deg": float(np.mean(yaw_errors)) if yaw_errors else None, "max_deviation_deg": max(yaw_errors, default=None),
                "mean_geometry_deviation_mm": float(np.mean(geometry_errors)) if geometry_errors else None,
                "collapsed": bool(collapsed),
                "repair_rounds": self.owner.repair_rounds, "simulation_time_s": world.t,
                "intent_match_score": self.critic_scores[-1] if self.critic_scores else None,
                "critic_is_mock": self.owner.llm_config["provider"] == "mock",
                "memory_rules": len(self.owner.memory.rules()) if self.variant == "B3" else 0,
                "slowdowns": sum(e.kind == "slowdown" for e in events), "stops": sum(e.kind == "stop" for e in events),
                "replans": sum(e.kind == "replan_ok" for e in events), "replan_failures": sum(e.kind == "replan_failed" for e in events),
                "path_length_rad": None if self.controller is None else self.controller.path_length,
                "minimum_obstacle_distance_m": None if self.controller is None or not np.isfinite(self.controller.minimum_distance) else self.controller.minimum_distance,
                **{f"interventions_{action}": self.interventions.get(action, 0) for action in ("ACCEPT", "REPOSITION", "REPLACE", "REPLAN_REMAINING", "RESEQUENCE", "WAIT", "ASK_USER")}}
            report = BuildReport(completed=not self.remaining and self.failure is None and not collapsed and self.collisions == 0, run_id=self.run.name,
                placed=len(self.done), total=len(self.done) + len(self.remaining), duration_s=time.perf_counter() - started,
                collisions=self.collisions, metrics=metrics, limitations=[
                    "Synthetic instance segmentation and idealized depth; finite sampled collision checks.",
                    "Collision metric counts contact ticks with scripted obstacles, not intended support/grasp contacts.",
                    "Corrective placements use calibrated stock without injected release noise.",
                    *([] if self.failure is None else [self.failure])])
            save_json(self.run / "report.json", report)
            from masonmind.metrics import enrich_metrics
            report.metrics.update(enrich_metrics(self.run, self.owner.llm_config, events,
                [] if self.controller is None else self.controller.replan_times))
            save_json(self.run / "report.json", report)
            from PIL import Image
            Image.fromarray(self.cameras[0].capture(unproject=False).rgb).save(self.run / "final.png")
            self.owner.emit("build_finished", report=report.model_dump())
            return report
