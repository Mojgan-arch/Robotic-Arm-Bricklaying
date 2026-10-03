"""Bounded generate/verify/repair orchestration, with no agent motion commands."""
from pathlib import Path
from collections.abc import Callable
from masonmind.config import load_config
from masonmind.io import create_run, save_json
from masonmind.llm.providers import make_client
from masonmind import agents
from masonmind.schemas import Design, AssemblySequence, BuildReport, SupervisorDecision
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
                 scenario: str, variant: str, video: bool):
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

    def observe(self) -> list[int]:
        """Detect unknown objects from depth and update query-only geometry."""
        from masonmind.planning.perception import detect_obstacles
        frames = [camera.capture() for camera in self.monitor_cameras]
        boxes = detect_obstacles(frames, self.world.known_ids, self.config["scan"])
        self.proxies.update_obstacles(boxes)
        self.checker.scene = [self.world.table, *self.proxies.bricks.values(), *self.proxies.obstacles]
        self.checker.support_bodies = {self.world.table, *self.proxies.bricks.values()}
        return self.proxies.obstacles

    def refresh_known(self) -> None:
        """Refresh known collision IDs after a nominal or scanned pose changes."""
        self.checker.scene = [self.world.table, *self.proxies.bricks.values(), *self.proxies.obstacles]
        self.checker.support_bodies = {self.world.table, *self.proxies.bricks.values()}

    def scan(self, bricks: list) -> object:
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
        np.savez_compressed(folder / "points.npz", **{f"view_{i}": f.points for i, f in enumerate(frames)})
        self.latest_scan, self.latest_scan_folder = result, folder
        if self.variant != "B0":
            for name, (position, yaw) in result.poses.items():
                self.proxies.update_brick(name, position, yaw)
            self.refresh_known()
        return result

    def supervisor(self, **evidence) -> SupervisorDecision:
        """Invoke a validated decision with measured evidence and current stock."""
        decision = self.owner.agent(agents.SiteSupervisor).decide({
            "course": evidence.pop("course", -1), "remaining_program": self.design.program,
            "supply_count": self.stock, "memory_rules": self.owner.memory_rules,
            "motion_events": [] if self.controller is None else self.controller.events[-8:], **evidence})
        from masonmind.io import append_json
        append_json(self.run / "decisions.jsonl", decision)
        self.decisions.append(decision)
        self.interventions[decision.action] = self.interventions.get(decision.action, 0) + 1
        return decision

    def supervised_plan(self, start, goal):
        """Retry the same task after a concrete supervisor intervention."""
        from masonmind.planning.rrt_connect import PlanningError
        for _ in range(self.config["construction"]["max_motion_retries"]):
            try:
                return self.controller.plan(self.arm.state() if _ else start, goal)
            except PlanningError as error:
                from masonmind.io import append_json
                append_json(self.run / "planning_failures.jsonl", {"phase": self.arm.phase, "error": str(error)})
                decision = self.supervisor(blocked=True, planning_error=str(error))
                if decision.action not in {"WAIT", "ASK_USER"}:
                    raise RuntimeError("An in-progress grasp requires WAIT/ASK_USER before changing tasks")
                self.apply_decision(decision)
        raise PlanningError("Supervisor motion retry budget exhausted")

    def supervised_execute(self, path) -> None:
        """Resume an interrupted primitive from current encoders, not from its start."""
        from masonmind.planning.rrt_connect import PlanningError
        goal = path[-1]
        for attempt in range(self.config["construction"]["max_motion_retries"]):
            try:
                self.controller.execute(path)
                return
            except PlanningError:
                decision = self.supervisor(blocked=True)
                self.apply_decision(decision)
                path = self.supervised_plan(self.arm.state(), goal)
        raise PlanningError("Supervisor execution retry budget exhausted")

    def place(self, brick, corrective: bool = False) -> None:
        """Consume stock and physically place it; correction runs use fresh calibrated stock."""
        if self.stock <= 0:
            raise RuntimeError("No stock available")
        self.stock -= 1
        body = self.world.spawn_brick(brick.id, length_scale=1 if corrective else self.disturbances.stock_length_scale())
        self.world.step(self.config["sim"]["settle_steps"])
        self.arm.release_hook = None if corrective else self.disturbances.on_release
        self.arm.drop_early = False if corrective else self.disturbances.should_drop()
        self.arm.pick_place(body, self.config["sim"]["supply"], [brick.x, brick.y, brick.z], brick.yaw_deg)
        self.arm.release_hook = None
        self.proxies.update_brick(brick.id, [brick.x, brick.y, brick.z], brick.yaw_deg)
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
            self.proxies.update_brick(name, [brick.x, brick.y, brick.z], brick.yaw_deg)
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
            if all(d.within_tolerance for d in scan.deviations) and not scan.broken_ids:
                break
            decision = self.supervisor(course=course, deviations=scan.deviations, broken_ids=scan.broken_ids)
            self.apply_decision(decision)
            self.incidents.append({"course": course, "action": decision.action, "brick_ids": decision.brick_ids})
            if decision.action == "ACCEPT":
                break
            scan = self.scan(bricks)
        else:
            raise RuntimeError(f"Course {course} still outside tolerance after intervention budget")

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
                self.controller.allow_replan = self.variant != "B1"
                self.controller.observe(force=True)
                self.arm.plan, self.arm.execute = self.supervised_plan, self.supervised_execute
            try:
                while self.remaining:
                    name = self.remaining[0]
                    brick = next(b for b in self.design.bricks if b.id == name)
                    self.course_events(brick.course)
                    self.place(brick)
                    self.done.append(name)
                    self.remaining.remove(name)
                    if not any(b.course == brick.course and b.id in self.remaining for b in self.design.bricks):
                        self.after_course(brick.course)
                self.scan([b for b in self.design.bricks if b.id in self.done])
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
                size_error = np.linalg.norm((actual_size - self.config["geometry"]["brick_size"]) / 2) * 1000
                geometry_errors.append(float(np.hypot(errors[-1], size_error)))
                collapsed |= abs(position[2] - brick.z) > self.config["geometry"]["brick_size"][2] / 2
            events = [] if self.controller is None else self.controller.events
            metrics = {"variant": self.variant, "scenario": self.scenario_name, "seed": self.config["seed"],
                "mean_deviation_mm": float(np.mean(errors)) if errors else None, "max_deviation_mm": max(errors, default=None),
                "mean_deviation_deg": float(np.mean(yaw_errors)) if yaw_errors else None, "max_deviation_deg": max(yaw_errors, default=None),
                "mean_geometry_deviation_mm": float(np.mean(geometry_errors)) if geometry_errors else None,
                "collapsed": bool(collapsed),
                "repair_rounds": self.owner.repair_rounds, "simulation_time_s": world.t,
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
            from PIL import Image
            Image.fromarray(self.cameras[0].capture(unproject=False).rgb).save(self.run / "final.png")
            return report
