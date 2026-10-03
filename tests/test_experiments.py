import csv
from experiments.run_all import write_results


def test_results_keep_failures_and_measure_diversity(tmp_path):
    rows = [
        {"variant": "B0", "scenario": "clean", "seed": 0, "completed": False, "design_signature": "same", "error": "failed"},
        {"variant": "B0", "scenario": "clean", "seed": 1, "completed": True, "design_signature": "same"},
        {"variant": "B3", "scenario": "clean", "seed": 0, "completed": True, "design_signature": "a"},
        {"variant": "B3", "scenario": "clean", "seed": 1, "completed": True, "design_signature": "b"},
    ]
    output = tmp_path / "results.csv"
    write_results(rows, output)
    with output.open(newline="") as stream:
        saved = list(csv.DictReader(stream))
    assert len(saved) == 4
    assert saved[0]["completed"] == "False"
    assert saved[0]["error"] == "failed"
    assert float(saved[0]["design_diversity_normalized"]) == 0
    assert float(saved[-1]["design_diversity_normalized"]) == 1
