"""Publication-friendly static comparison plots from measured CSV rows."""
import argparse
import csv
import os
from pathlib import Path
import numpy as np


def make_plots(source: Path, output: Path) -> list[Path]:
    """Plot completion, displacement, timing and interventions; never impute success."""
    os.environ.setdefault("MPLCONFIGDIR", str(output.resolve() / ".matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    with source.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("Results CSV contains no trials")
    variants = sorted({row["variant"] for row in rows})
    colors = ["#94a3b8", "#38bdf8", "#6366f1", "#14b8a6"][:len(variants)]
    def mean(variant: str, key: str) -> float:
        values = [float(row[key]) for row in rows if row["variant"] == variant and row.get(key) not in (None, "", "None")]
        return float(np.mean(values)) if values else float("nan")
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), constrained_layout=True)
    completion = [np.mean([row["completed"].lower() == "true" for row in rows if row["variant"] == variant]) for variant in variants]
    metrics = [("Completed without detected collapse/collision", completion, "fraction"),
               ("Mean final displacement (observed trials)", [mean(v, "mean_deviation_mm") for v in variants], "mm"),
               ("Mean wall-clock duration", [mean(v, "duration_s") for v in variants], "seconds"),
               ("Supervisor interventions", [sum(mean(v, "interventions_" + a) for a in ("REPOSITION", "REPLACE", "WAIT", "ASK_USER")) for v in variants], "mean decisions")]
    for ax, (title, values, unit) in zip(axes.flat, metrics):
        ax.bar(variants, values, color=colors)
        ax.set_title(title, fontsize=11)
        ax.set_ylabel(unit)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=.2)
    mock = any(row.get("critic_is_mock", "").lower() == "true" for row in rows)
    fig.suptitle(f"MasonMind | {len(rows)} simulated trials" + (" | mock agents (integration benchmark)" if mock else ""), fontsize=14)
    output.mkdir(parents=True, exist_ok=True)
    png, pdf = output / "comparison.png", output / "comparison.pdf"
    fig.savefig(png, dpi=180, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    scenarios = sorted({row["scenario"] for row in rows})
    matrix = np.array([[np.mean([row["completed"].lower() == "true" for row in rows if row["scenario"] == scenario and row["variant"] == variant])
                        for variant in variants] for scenario in scenarios])
    fig, ax = plt.subplots(figsize=(7, 6), constrained_layout=True)
    image = ax.imshow(matrix, vmin=0, vmax=1, cmap="YlGnBu", aspect="auto")
    ax.set_xticks(range(len(variants)), variants)
    ax.set_yticks(range(len(scenarios)), scenarios)
    for y in range(len(scenarios)):
        for x in range(len(variants)):
            ax.text(x, y, f"{matrix[y, x]:.0%}", ha="center", va="center", color="white" if matrix[y, x] > .6 else "black")
    ax.set_title("Completion by scenario; failed trials retained")
    fig.colorbar(image, ax=ax, label="Completion fraction")
    heatmap = output / "completion.png"
    fig.savefig(heatmap, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return [png, pdf, heatmap]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?", default=Path("experiments/results.csv"))
    args = parser.parse_args()
    make_plots(args.source, args.source.parent / "figures")
