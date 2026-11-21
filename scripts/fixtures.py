"""Explicit synthetic schemas, duplicate rows and plans for the study."""

from relab.engine import Table


def scan(name):
    return {"op": "scan", "table": name}


def compare(op, column, literal):
    return {"op": op, "args": [{"column": column}, {"literal": literal}]}


def select(plan, predicate):
    return {"op": "select", "input": plan, "predicate": predicate}


def project(plan, names):
    return {"op": "project", "input": plan, "columns": names}


def join(kind="inner", algorithm="nested"):
    return {
        "op": "join",
        "kind": kind,
        "algorithm": algorithm,
        "left": scan("left"),
        "right": scan("right"),
        "keys": [["l.key", "r.key"]],
    }


def small_tables():
    return {
        "left": Table(
            (("l.key", "integer"), ("l.value", "text")),
            (
                (1, "alpha"),
                (1, "alpha"),
                (2, "beta"),
                (None, "unknown"),
                (-1, "minus-one"),
                (-2, "minus-two"),
                (9, "unmatched"),
            ),
        ),
        "right": Table(
            (("r.key", "integer"), ("r.score", "integer")),
            ((1, 8), (1, 12), (2, None), (None, 99), (-1, 4), (-2, 5)),
        ),
    }


def scenarios():
    tables = small_tables()
    outer_where = select(join("left"), compare("gt", "r.score", 10))
    unsafe = join("left")
    unsafe["right"] = select(unsafe["right"], compare("gt", "r.score", 10))
    return {
        "duplicate_inner": (tables, join("inner")),
        "null_left": (tables, join("left")),
        "selective_inner": (
            tables,
            project(select(join(), compare("eq", "l.key", 1)), ["l.value", "r.score"]),
        ),
        "left_preserved_pushdown": (
            tables,
            select(join("left"), compare("gt", "l.key", 0)),
        ),
        "outer_where": (tables, outer_where),
        "unsafe_right_pushdown": (tables, unsafe),
        "empty_projection": (tables, project(scan("left"), [])),
        "empty_schema": (
            {"empty": Table((), ((), (), ()))},
            select(scan("empty"), {"op": "true"}),
        ),
        "empty_input": (
            {"left": Table(tables["left"].columns, ()), "right": tables["right"]},
            join("left"),
        ),
    }


def benchmark_tables(size=320, skew=False):
    left = tuple((index % 4 if skew else index, index % 80) for index in range(size))
    right = tuple((index % 4 if skew else index, index) for index in range(size))
    return {
        "left": Table((("l.key", "integer"), ("l.score", "integer")), left),
        "right": Table((("r.key", "integer"), ("r.score", "integer")), right),
    }


def benchmark_plan(algorithm="nested", pushdown=False):
    plan = join("inner", algorithm)
    filtered = select(plan, compare("lt", "l.score", 3))
    if pushdown:
        plan["left"] = select(plan["left"], compare("lt", "l.score", 3))
        return plan
    return filtered
