"""Execute fixed plans, preserve SQLite comparisons, and measure local operators."""

import argparse
from collections import Counter
import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import random
import sqlite3
import subprocess
import sys
from time import perf_counter_ns

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from relab.engine import Table, execute, optimize
from relab.oracle import sqlite_result
from scripts.fixtures import (
    scenarios,
    benchmark_plan,
    benchmark_tables,
    compare,
    join,
    select,
    project,
)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def write_json(path, value):
    write(path, json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def source_hashes():
    paths = [
        *ROOT.glob("relab/*.py"),
        *(file for file in ROOT.glob("tests/*.py") if file.name != "test_media.py"),
    ]
    paths += [
        ROOT / "scripts" / name
        for name in ("fixtures.py", "run_study.py", "report.py", "verify_results.py")
    ]
    return {
        path.relative_to(ROOT)
        .as_posix(): hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n"))
        .hexdigest()
        for path in sorted(paths)
    }


def bag(rows):
    return Counter(map(tuple, rows))


def hashify(plan):
    plan = copy.deepcopy(plan)

    def walk(node):
        if node["op"] == "join":
            node["algorithm"] = "hash"
        for key in ("input", "left", "right"):
            if key in node:
                walk(node[key])

    walk(plan)
    return plan


def evidence(tables, plan):
    optimized, changes = optimize(plan, tables)
    hashed = hashify(optimized)
    result = execute(plan, tables)
    oracle = sqlite_result(plan, tables)
    optimized_result = execute(optimized, tables)
    hash_result = execute(hashed, tables)
    assert (
        bag(result["rows"])
        == bag(oracle["rows"])
        == bag(optimized_result["rows"])
        == bag(hash_result["rows"])
    )
    return {
        "input": {
            "tables": {name: table.to_dict() for name, table in tables.items()},
            "plan": plan,
        },
        "original": result,
        "sqlite": oracle,
        "optimized_plan": optimized,
        "rewrites": changes,
        "optimized": optimized_result,
        "hash_plan": hashed,
        "hash": hash_result,
    }


def study(out, samples, random_cases):
    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    write(out / "results/test-results.txt", tests.stdout + tests.stderr)
    print(tests.stdout + tests.stderr)
    tests.check_returncode()
    summary = []
    for label, (tables, plan) in scenarios().items():
        record = evidence(tables, plan)
        write_json(out / "examples" / f"{label}.json", record["input"])
        write_json(out / "results/cases" / f"{label}.json", record)
        summary.append(
            {
                "scenario": label,
                "output_rows": len(record["original"]["rows"]),
                "rewrites": record["rewrites"],
                "sqlite_equal": True,
            }
        )
    rng = random.Random(501862)
    corpus = []
    for case in range(random_cases):
        tables = {
            "left": Table(
                (("l.key", "integer"), ("l.score", "integer")),
                tuple(
                    (rng.choice([None, -2, -1, 0, 1, 2]), rng.choice([None, 0, 1, 3]))
                    for _ in range(rng.randrange(10))
                ),
            ),
            "right": Table(
                (("r.key", "integer"), ("r.score", "integer")),
                tuple(
                    (rng.choice([None, -2, -1, 0, 1, 2]), rng.choice([None, 0, 1, 3]))
                    for _ in range(rng.randrange(10))
                ),
            ),
        }
        plan = join(rng.choice(["inner", "left"]))
        predicate = rng.choice(
            [
                compare("le", "l.score", 1),
                compare("ne", "r.key", 2),
                {"op": "is_null", "args": [{"column": "r.score"}]},
            ]
        )
        if case % 3 == 0:
            predicate = {
                "op": "and",
                "args": [
                    predicate,
                    {"op": "not", "args": [compare("eq", "l.key", None)]},
                ],
            }
        plan = select(plan, predicate)
        if case % 4 == 0:
            plan = project(plan, [] if case % 8 == 0 else ["l.key", "r.score"])
        corpus.append({"case": case, **evidence(tables, plan)})
    write_json(out / "results/oracle-corpus.json", corpus)
    write_json(out / "results/summary.json", summary)

    configurations = {}
    benchmark_records = []
    for workload, skew in (("uniform", False), ("skewed", True)):
        tables = benchmark_tables(320, skew)
        baseline = None
        for algorithm in ("nested", "hash"):
            for pushdown in (False, True):
                label = f"{workload}_{algorithm}_{'pushdown' if pushdown else 'late_filter'}"
                plan = benchmark_plan(algorithm, pushdown)
                result = execute(plan, tables)
                oracle = sqlite_result(plan, tables)
                assert bag(result["rows"]) == bag(oracle["rows"])
                if baseline is None:
                    baseline = bag(result["rows"])
                assert baseline == bag(result["rows"])
                configurations[label] = (tables, plan)
                record = {
                    "configuration": label,
                    "input": {
                        "tables": {
                            name: table.to_dict() for name, table in tables.items()
                        },
                        "plan": plan,
                    },
                    "result": result,
                    "sqlite": oracle,
                }
                write_json(out / "results/benchmarks" / f"{label}.json", record)
                benchmark_records.append(
                    {
                        "configuration": label,
                        "output_rows": len(result["rows"]),
                        "candidate_pairs": sum(
                            row["candidate_pairs"] for row in result["stats"].values()
                        ),
                        "predicate_evaluations": sum(
                            row["predicate_evaluations"]
                            for row in result["stats"].values()
                        ),
                        "operator_output_total": sum(
                            row["output_rows"] for row in result["stats"].values()
                        ),
                    }
                )
                execute(plan, tables)  # One untimed warmup per configuration.
    order = [label for _ in range(samples) for label in configurations]
    random.Random(76214).shuffle(order)
    rows = []
    for index, label in enumerate(order, 1):
        tables, plan = configurations[label]
        start = perf_counter_ns()
        result = execute(plan, tables)
        elapsed = perf_counter_ns() - start
        rows.append(
            {
                "ordinal": index,
                "configuration": label,
                "elapsed_ns": elapsed,
                "output_rows": len(result["rows"]),
            }
        )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write(out / "results/timings.csv", stream.getvalue())
    write_json(out / "results/benchmark-summary.json", benchmark_records)
    write_json(
        out / "results/environment.json",
        {
            "recorded_utc": datetime.now(timezone.utc).isoformat(),
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "sqlite_version": sqlite3.sqlite_version,
            "random_cases": random_cases,
            "random_seed": 501862,
            "timing_order_seed": 76214,
            "samples_per_configuration": samples,
            "timing_scope": "execute includes plan copy/validation, iterator execution, counters and final materialization; excludes SQLite, JSON encoding, disk I/O and plotting",
            "benchmark_rows_per_input": 320,
            "warmups_per_configuration": 1,
        },
    )
    from scripts.report import generate

    generate(out)
    if out != ROOT:
        for name in ("semantics.md", "optimizer.md", "methodology.md"):
            write(
                out / "docs" / name, (ROOT / "docs" / name).read_text(encoding="utf-8")
            )
    paths = [
        *out.glob("examples/*.json"),
        *out.glob("results/**/*"),
        *out.glob("docs/figures/*.svg"),
        out / "docs/report.html",
    ]
    write_json(
        out / "results/provenance.json",
        {
            "source_sha256_lf": source_hashes(),
            "artifact_sha256": {
                path.relative_to(out)
                .as_posix(): hashlib.sha256(path.read_bytes())
                .hexdigest()
                for path in sorted(paths)
                if path.is_file() and path.name != "provenance.json"
            },
        },
    )
    from scripts.verify_results import verify_artifacts

    verify_artifacts(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--random-cases", type=int, default=80)
    args = parser.parse_args()
    if not 1 <= args.samples <= 30 or not 1 <= args.random_cases <= 300:
        parser.error("samples must be 1..30 and random-cases 1..300")
    study(args.output_dir.resolve(), args.samples, args.random_cases)
