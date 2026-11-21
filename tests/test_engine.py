from collections import Counter
import copy
import random
import sqlite3
import unittest

from relab.engine import Table, execute, evaluate, optimize, tables_from_dict, validate
from relab.oracle import sqlite_result
from scripts.fixtures import scenarios, join, scan, select, project, compare


def bag(rows):
    return Counter(map(tuple, rows))


class EngineTests(unittest.TestCase):
    def test_complete_three_valued_boolean_tables_against_sqlite(self):
        connection = sqlite3.connect(":memory:")
        try:
            for first in (True, False, None):
                for second in (True, False, None):
                    constants = [
                        {"op": {True: "true", False: "false", None: "unknown"}[value]}
                        for value in (first, second)
                    ]
                    for op in ("and", "or"):
                        actual = evaluate({"op": op, "args": constants}, (), {})
                        sql = connection.execute(
                            f"SELECT ? {op} ?", (first, second)
                        ).fetchone()[0]
                        self.assertEqual(actual, None if sql is None else bool(sql))
                result = evaluate(
                    {
                        "op": "not",
                        "args": [
                            {
                                "op": {True: "true", False: "false", None: "unknown"}[
                                    first
                                ]
                            }
                        ],
                    },
                    (),
                    {},
                )
                sql = connection.execute("SELECT NOT ?", (first,)).fetchone()[0]
                self.assertEqual(result, None if sql is None else bool(sql))
        finally:
            connection.close()

    def test_all_fixed_cases_and_bag_multiplicity(self):
        for label, (tables, plan) in scenarios().items():
            with self.subTest(case=label):
                expected = sqlite_result(plan, tables)
                actual = execute(plan, tables)
                self.assertEqual(bag(actual["rows"]), bag(expected["rows"]))
                optimized, _ = optimize(plan, tables)
                self.assertEqual(
                    bag(actual["rows"]), bag(execute(optimized, tables)["rows"])
                )
        tables, plan = scenarios()["duplicate_inner"]
        rows = execute(plan, tables)["rows"]
        self.assertEqual(bag(rows)[(1, "alpha", 1, 8)], 2)
        self.assertEqual(bag(rows)[(1, "alpha", 1, 12)], 2)
        self.assertEqual(
            len(execute(scenarios()["empty_projection"][1], tables)["rows"]), 7
        )

    def test_360_seeded_graphs_plans_against_sqlite(self):
        rng = random.Random(938172)
        for case in range(360):
            left = tuple(
                (rng.choice([None, -2, -1, 0, 1, 2]), rng.choice([None, 0, 1, 2]))
                for _ in range(rng.randrange(9))
            )
            right = tuple(
                (rng.choice([None, -2, -1, 0, 1, 2]), rng.choice([None, 0, 1, 2]))
                for _ in range(rng.randrange(9))
            )
            tables = {
                "left": Table((("l.key", "integer"), ("l.score", "integer")), left),
                "right": Table((("r.key", "integer"), ("r.score", "integer")), right),
            }
            plan = join(rng.choice(["inner", "left"]))
            predicates = [
                compare("eq", "l.key", rng.choice([None, -1, 1])),
                compare("gt", "r.score", 0),
                {"op": "is_null", "args": [{"column": "r.score"}]},
            ]
            chosen = rng.choice(predicates)
            if case % 4 == 0:
                chosen = {"op": "or", "args": [chosen, compare("eq", "l.key", -2)]}
            if case % 5 == 0:
                chosen = {"op": "not", "args": [chosen]}
            plan = select(plan, chosen)
            if case % 3 == 0:
                plan = project(plan, [] if case % 9 == 0 else ["l.key", "r.score"])
            with self.subTest(case=case):
                expected = bag(sqlite_result(plan, tables)["rows"])
                self.assertEqual(bag(execute(plan, tables)["rows"]), expected)
                alternate = copy.deepcopy(plan)

                def hashify(node):
                    if node["op"] == "join":
                        node["algorithm"] = "hash"
                    for child in ("input", "left", "right"):
                        if child in node:
                            hashify(node[child])

                hashify(alternate)
                self.assertEqual(bag(execute(alternate, tables)["rows"]), expected)
                optimized, _ = optimize(alternate, tables)
                self.assertEqual(bag(execute(optimized, tables)["rows"]), expected)

    def test_hash_collision_is_not_equality_and_null_never_matches(self):
        self.assertEqual(hash(-1), hash(-2))
        tables, plan = scenarios()["duplicate_inner"]
        nested = execute(plan, tables)
        plan["algorithm"] = "hash"
        hashed = execute(plan, tables)
        self.assertEqual(bag(nested["rows"]), bag(hashed["rows"]))
        self.assertFalse(any(row[0] is None for row in hashed["rows"]))
        self.assertFalse(any(row[0] == -1 and row[2] == -2 for row in hashed["rows"]))
        self.assertLess(
            hashed["stats"]["root"]["candidate_pairs"],
            nested["stats"]["root"]["candidate_pairs"],
        )

    def test_outer_join_counterexample_and_optimizer_boundary(self):
        tables, original = scenarios()["outer_where"]
        unsafe = scenarios()["unsafe_right_pushdown"][1]
        correct = execute(original, tables)
        wrong = execute(unsafe, tables)
        self.assertNotEqual(bag(correct["rows"]), bag(wrong["rows"]))
        self.assertEqual(len(correct["rows"]), 2)
        self.assertEqual(len(wrong["rows"]), 7)
        optimized, changes = optimize(original, tables)
        self.assertEqual(optimized, original)
        self.assertEqual(changes, [])

    def test_composite_keys_nested_selection_and_projection_rewrites(self):
        tables = {
            "left": Table(
                (("l.key", "integer"), ("l.text", "text")),
                ((1, "a"), (1, None), (1, "b")),
            ),
            "right": Table(
                (("r.key", "integer"), ("r.text", "text")),
                ((1, "a"), (1, None), (1, "b")),
            ),
        }
        plan = join("left", "hash")
        plan["keys"].append(["l.text", "r.text"])
        plan = project(
            project(
                select(
                    select(plan, compare("ge", "l.key", 0)),
                    compare("ne", "l.text", "b"),
                ),
                ["l.key", "l.text", "r.text"],
            ),
            ["l.key"],
        )
        optimized, changes = optimize(plan, tables)
        self.assertIn("collapse_projections", changes)
        self.assertEqual(
            bag(execute(optimized, tables)["rows"]),
            bag(sqlite_result(plan, tables)["rows"]),
        )

    def test_validation_and_execution_budgets(self):
        for value in (True, 2**63, 1.0, b"x", "\x00"):
            with self.assertRaises(ValueError):
                Table((("x", "integer"),), ((value,),))
        tables, plan = scenarios()["duplicate_inner"]
        with self.assertRaises(RuntimeError):
            execute(plan, tables, pair_limit=1)
        with self.assertRaises(RuntimeError):
            execute(plan, tables, output_limit=1)
        for mutate in [
            lambda p: p.update(kind="full"),
            lambda p: p.update(keys=[]),
            lambda p: p.update(extra=True),
            lambda p: p["keys"][0].__setitem__(1, "r.missing"),
            lambda p: p.update(left=scan("missing")),
        ]:
            bad = copy.deepcopy(plan)
            mutate(bad)
            with self.assertRaises(ValueError):
                validate(bad, tables)
        self.assertEqual(
            tables_from_dict({name: table.to_dict() for name, table in tables.items()}),
            tables,
        )

    def test_text_binary_collation_and_integer_boundaries_match_sqlite(self):
        tables = {
            "left": Table(
                (("l.key", "text"), ("l.value", "integer")),
                (
                    ("A", -(2**63)),
                    ("a", 2**63 - 1),
                    ("é", 0),
                    ("水", None),
                    ("😀", 1),
                    (None, 2),
                ),
            ),
            "right": Table(
                (("r.key", "text"), ("r.value", "integer")),
                (("a", 8), ("é", 9), ("😀", None), (None, 10)),
            ),
        }
        for algorithm in ("nested", "hash"):
            for operator in ("eq", "ne", "lt", "le", "gt", "ge"):
                plan = select(join("left", algorithm), compare(operator, "l.key", "é"))
                self.assertEqual(
                    bag(execute(plan, tables)["rows"]),
                    bag(sqlite_result(plan, tables)["rows"]),
                )
        plan = select(scan("left"), compare("lt", "l.value", 2**63 - 1))
        self.assertEqual(
            bag(execute(plan, tables)["rows"]), bag(sqlite_result(plan, tables)["rows"])
        )


if __name__ == "__main__":
    unittest.main()
