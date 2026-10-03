"""Run a genuine variant x scenario x seed matrix and preserve failed trials."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from pathlib import Path
from masonmind.config import load_config, ROOT
from masonmind.io import create_run
from masonmind.orchestrator import Orchestrator

SCENARIOS = ["clean", "noisy_placement", "drops", "broken_bricks", "material_shortage", "user_change", "moving_obstacle", "blocking_obstacle", "everything"]
VARIANTS = ["B0", "B1", "B2", "B3"]


def run_batch(job: tuple) -> list[dict]:
    """Keep each variant/seed's memory sequential and isolated from other groups."""
    variant, seed, scenarios, quick, root = job
    rows = []
    memory_path = Path(root) / f"memory_{variant}_{seed}.jsonl"
    for scenario in scenarios:
        cfg = load_config()
        cfg["seed"], cfg["variant"] = seed, variant
        cfg["memory"].update(enabled=variant == "B3", path=str(memory_path))
        if quick:
            cfg["sim"]["cartesian_step_m"] = .02
            cfg["scan"].update(monitor_width=96, monitor_height=72)
        llm = load_config("llm_mock" if quick else "llm")
        llm["cache"] = False
        run = create_run(Path(root) / "trials")
        owner = Orchestrator(cfg, llm, run)
        try:
            design, sequence = owner.run_phase_a("A stable three-course wall")
            report = owner.run_phase_b(design, sequence, scenario, variant, video=False)
            rows.append({"variant": variant, "scenario": scenario, "seed": seed,
                         "completed": report.completed, "duration_s": report.duration_s,
                         "collisions": report.collisions, "run": str(run.resolve()), "quick": quick,
                         "error": "; ".join(report.limitations[3:]), **report.metrics})
        except Exception as error:
            # A failed trial is an explicit result, not a reason to lose the matrix.
            rows.append({"variant": variant, "scenario": scenario, "seed": seed, "completed": False,
                         "run": str(run.resolve()), "quick": quick, "error": f"{type(error).__name__}: {error}"})
    return rows


def write_results(rows: list[dict], output: Path) -> None:
    """Write the current matrix atomically, with explicit cross-seed diversity."""
    for row in rows:
        group = [r for r in rows if (r["variant"], r["scenario"]) == (row["variant"], row["scenario"]) and r.get("design_signature")]
        signatures = {r["design_signature"] for r in group}
        row["design_diversity_unique"] = len(signatures)
        row["design_diversity_normalized"] = (len(signatures) - 1) / (len(group) - 1) if len(group) > 1 else None
    fields = ["variant", "scenario", "seed", "completed", "duration_s", "collisions"]
    fields += sorted({key for row in rows for key in row} - set(fields))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["variant"], r["scenario"], r["seed"])))
    temporary.replace(output)


def main() -> None:
    """Run the full matrix; quick uses one seed and coarser approach sampling."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=SCENARIOS)
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=VARIANTS)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/results.csv")
    args = parser.parse_args()
    if args.seeds < 1 or args.jobs < 1:
        parser.error("seeds and jobs must be positive")
    root = create_run(ROOT / "runs/experiments")
    jobs = [(variant, seed, args.scenarios, args.quick, str(root)) for variant in args.variants for seed in range(1 if args.quick else args.seeds)]
    rows = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_batch, job) for job in jobs]
        for future in as_completed(futures):
            rows.extend(future.result())
            write_results(rows, args.output)
            print(f"Saved {len(rows)} trials -> {args.output}", flush=True)
    if __package__:
        from .plots import make_plots
    else:
        from plots import make_plots
    make_plots(args.output, args.output.parent / "figures")
    print(f"Matrix: {sum(bool(r['completed']) for r in rows)}/{len(rows)} completed. Failures remain in the CSV.")


if __name__ == "__main__":
    main()
