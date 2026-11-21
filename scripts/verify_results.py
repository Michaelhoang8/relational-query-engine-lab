"""Recheck exact bags against fresh SQLite and replay preserved operator counters."""

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from relab.engine import execute, optimize, tables_from_dict
from relab.oracle import sqlite_result
from scripts.run_study import source_hashes


def bag(rows):
    return Counter(map(tuple, rows))


def check_record(record):
    tables = tables_from_dict(record["input"]["tables"])
    plan = record["input"]["plan"]
    assert execute(plan, tables) == record["original"]
    sql = sqlite_result(plan, tables)
    assert sql["query"] == record["sqlite"]["query"]
    assert sql["parameters"] == record["sqlite"]["parameters"]
    assert (
        bag(sql["rows"])
        == bag(record["original"]["rows"])
        == bag(record["sqlite"]["rows"])
    )
    optimized, rewrites = optimize(plan, tables)
    assert optimized == record["optimized_plan"] and rewrites == record["rewrites"]
    assert execute(optimized, tables) == record["optimized"]
    assert execute(record["hash_plan"], tables) == record["hash"]
    assert (
        bag(record["hash"]["rows"])
        == bag(record["optimized"]["rows"])
        == bag(record["original"]["rows"])
    )


def verify_artifacts(out):
    provenance = json.loads((out / "results/provenance.json").read_text())
    assert source_hashes() == provenance["source_sha256_lf"]
    for name, expected in provenance["artifact_sha256"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == expected, name
    fixed = {}
    for file in sorted((out / "results/cases").glob("*.json")):
        record = json.loads(file.read_text())
        check_record(record)
        fixed[file.stem] = record
        assert json.loads((out / "examples" / file.name).read_text()) == record["input"]
    assert len(fixed["outer_where"]["original"]["rows"]) == 2
    assert len(fixed["unsafe_right_pushdown"]["original"]["rows"]) == 7
    assert bag(fixed["outer_where"]["original"]["rows"]) != bag(
        fixed["unsafe_right_pushdown"]["original"]["rows"]
    )
    for row in json.loads((out / "results/summary.json").read_text()):
        record = fixed[row["scenario"]]
        assert (
            row["output_rows"] == len(record["original"]["rows"])
            and row["rewrites"] == record["rewrites"]
        )
    corpus = json.loads((out / "results/oracle-corpus.json").read_text())
    for row in corpus:
        check_record(row)
    benchmark = json.loads((out / "results/benchmark-summary.json").read_text())
    results_by_workload = {}
    for row in benchmark:
        record = json.loads(
            (out / "results/benchmarks" / f'{row["configuration"]}.json').read_text()
        )
        tables = tables_from_dict(record["input"]["tables"])
        actual = execute(record["input"]["plan"], tables)
        assert actual == record["result"]
        oracle = sqlite_result(record["input"]["plan"], tables)
        assert (
            bag(actual["rows"]) == bag(record["sqlite"]["rows"]) == bag(oracle["rows"])
        )
        assert row["output_rows"] == len(actual["rows"])
        assert row["candidate_pairs"] == sum(
            value["candidate_pairs"] for value in actual["stats"].values()
        )
        assert row["predicate_evaluations"] == sum(
            value["predicate_evaluations"] for value in actual["stats"].values()
        )
        assert row["operator_output_total"] == sum(
            value["output_rows"] for value in actual["stats"].values()
        )
        workload = row["configuration"].split("_")[0]
        if workload in results_by_workload:
            assert results_by_workload[workload] == bag(actual["rows"])
        results_by_workload[workload] = bag(actual["rows"])
    # Independently derived join-work counts for the predeclared 320-row fixtures.
    expected_pairs = {
        "uniform_nested_late_filter": 102400,
        "uniform_nested_pushdown": 3840,
        "uniform_hash_late_filter": 320,
        "uniform_hash_pushdown": 12,
        "skewed_nested_late_filter": 102400,
        "skewed_nested_pushdown": 3840,
        "skewed_hash_late_filter": 25600,
        "skewed_hash_pushdown": 960,
    }
    assert {
        row["configuration"]: row["candidate_pairs"] for row in benchmark
    } == expected_pairs
    with (out / "results/timings.csv").open(newline="", encoding="utf-8") as handle:
        timing = list(csv.DictReader(handle))
    environment = json.loads((out / "results/environment.json").read_text())
    assert len(timing) == len(benchmark) * environment["samples_per_configuration"]
    counts = {row["configuration"]: row["output_rows"] for row in benchmark}
    for index, row in enumerate(timing, 1):
        assert int(row["ordinal"]) == index and int(row["elapsed_ns"]) > 0
        assert int(row["output_rows"]) == counts[row["configuration"]]
    assert len(corpus) == environment["random_cases"]
    print(
        f"Verified {len(fixed)} fixed cases, {len(corpus)} preserved randomized cases, {len(benchmark)} benchmark plans and {len(timing)} actual timing samples."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    verify_artifacts(parser.parse_args().output_dir.resolve())
