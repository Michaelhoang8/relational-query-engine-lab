"""Execute an explicit JSON relational plan; no general SQL parser."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from .engine import tables_from_dict, execute, optimize
from .oracle import sqlite_result


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--optimize", action="store_true")
    parser.add_argument("--sqlite-check", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("build/query-result.json"))
    args = parser.parse_args()
    try:
        if args.input.stat().st_size > 2_000_000:
            raise ValueError("input exceeds 2 MB")
        document = json.loads(
            args.input.read_text(encoding="utf-8"), object_pairs_hook=unique_object
        )
        if not isinstance(document, dict) or set(document) != {"tables", "plan"}:
            raise ValueError("input fields must be tables and plan")
        tables = tables_from_dict(document["tables"])
        plan = document["plan"]
        changes = []
        if args.optimize:
            plan, changes = optimize(plan, tables)
        result = execute(plan, tables)
        output = {"plan": plan, "rewrites": changes, "result": result}
        if args.sqlite_check:
            oracle = sqlite_result(plan, tables)
            if Counter(map(tuple, result["rows"])) != Counter(
                map(tuple, oracle["rows"])
            ):
                raise RuntimeError("SQLite bag comparison failed")
            output["sqlite"] = oracle
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"Saved {len(result['rows'])} rows to {args.output}")
        return 0
    except (OSError, ValueError, TypeError, RuntimeError, RecursionError) as error:
        print(f"relab: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
