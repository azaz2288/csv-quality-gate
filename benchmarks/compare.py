"""Reproducible synthetic profile comparison cost, not CSV parsing throughput."""
import argparse
import copy
import json
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csvgate.core import compare


def run(columns=512, rounds=30):
    if type(columns) is not int or not 1 <= columns <= 10000:
        raise ValueError("columns must be 1..10000")
    if type(rounds) is not int or not 1 <= rounds <= 100:
        raise ValueError("rounds must be 1..100")
    names = [f"value-{index}" for index in range(columns)]
    old = {"version": 1, "source_sha256": "0" * 64, "rows": 2, "columns": names,
           "missing": dict.fromkeys(names, 0),
           "numeric": {name: {"count": 2, "mean": 0, "stddev": 3, "min": -3, "max": 3}
                       for name in names},
           "categories": {}, "keys": {"columns": [], "missing_rows": 0, "duplicate_rows": 0}}
    new = copy.deepcopy(old)
    for stat in new["numeric"].values():
        stat.update(mean=1, stddev=0, min=1, max=1)
    compare(old, new, 1, 1 / 3, 1)  # independent warmup, excluded from timing
    timings = []
    for _ in range(rounds):
        start = time.perf_counter()
        report = compare(old, new, 1, 1 / 3, 1)
        timings.append(time.perf_counter() - start)
        assert not report["passed"] and len(report["violations"]) == columns
    # Separate allocation pass; timings above do not include tracemalloc.
    tracemalloc.start()
    try:
        report = compare(old, new, 1, 1 / 3, 1)
        _, peak = tracemalloc.get_traced_memory()
        assert len(report["violations"]) == columns
    finally:
        tracemalloc.stop()
    return {"python": platform.python_version(), "platform": platform.platform(),
            "synthetic_numeric_columns": columns, "rounds": rounds,
            "median_seconds": statistics.median(timings), "min_seconds": min(timings),
            "max_seconds": max(timings), "python_allocation_peak_bytes": peak,
            "expected_violations_each_round": columns,
            "scope": "profile comparison only; no CSV parsing or native RSS; not a before/after speedup"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--columns", type=int, default=512)
    parser.add_argument("--rounds", type=int, default=30)
    options = parser.parse_args()
    print(json.dumps(run(options.columns, options.rounds), indent=2))
