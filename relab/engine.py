"""Bag-valued relational execution with strict, explicit plan schemas."""

from collections import defaultdict
from dataclasses import dataclass
import copy
import re

MAX_OUTPUT = 100_000
MAX_PAIRS = 2_000_000


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_.]{0,31}", value
    ):
        raise ValueError("identifiers must be 1..32 ASCII identifier characters")
    return value


def value_type(value):
    if value is None:
        return None
    if type(value) is int and -(2**63) <= value < 2**63:
        return "integer"
    if type(value) is str and len(value) <= 256 and "\x00" not in value:
        # Exclude surrogate code points, which cannot round-trip UTF-8 SQLite text.
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("text must be valid UTF-8") from error
        return "text"
    raise ValueError(
        "values must be NULL, signed 64-bit integers, or UTF-8 text <=256 characters"
    )


@dataclass(frozen=True)
class Table:
    columns: tuple[tuple[str, str], ...]
    rows: tuple[tuple, ...]

    def __post_init__(self):
        if type(self.columns) is not tuple or len(self.columns) > 16:
            raise ValueError("table columns must be a tuple with at most 16 columns")
        names = set()
        for item in self.columns:
            if type(item) is not tuple or len(item) != 2:
                raise ValueError("column must be a (name,type) tuple")
            name, dtype = item
            identifier(name)
            if name in names or dtype not in ("integer", "text"):
                raise ValueError("duplicate column or unsupported type")
            names.add(name)
        if type(self.rows) is not tuple or len(self.rows) > 1024:
            raise ValueError("rows must be a tuple of at most 1024 rows")
        for row in self.rows:
            if type(row) is not tuple or len(row) != len(self.columns):
                raise ValueError("row shape mismatch")
            for value, (_, dtype) in zip(row, self.columns):
                actual = value_type(value)
                if actual is not None and actual != dtype:
                    raise ValueError("row value does not match column type")

    def to_dict(self):
        return {
            "columns": [list(column) for column in self.columns],
            "rows": [list(row) for row in self.rows],
        }


def tables_from_dict(data):
    if not isinstance(data, dict) or not 1 <= len(data) <= 8:
        raise ValueError("expected 1..8 named tables")
    tables = {}
    for name, value in data.items():
        identifier(name)
        if not isinstance(value, dict) or set(value) != {"columns", "rows"}:
            raise ValueError("table fields must be columns and rows")
        if not isinstance(value["columns"], list) or not isinstance(
            value["rows"], list
        ):
            raise ValueError("columns and rows must be arrays")
        if any(not isinstance(row, list) for row in value["columns"] + value["rows"]):
            raise ValueError("column and row entries must be arrays")
        tables[name] = Table(
            tuple(tuple(column) for column in value["columns"]),
            tuple(tuple(row) for row in value["rows"]),
        )
    return tables


def scalar_type(scalar, schema):
    if not isinstance(scalar, dict) or len(scalar) != 1:
        raise ValueError("scalar must contain exactly column or literal")
    if "column" in scalar:
        if not isinstance(scalar["column"], str) or scalar["column"] not in dict(
            schema
        ):
            raise ValueError("unknown column")
        return dict(schema)[scalar["column"]]
    if "literal" in scalar:
        return value_type(scalar["literal"])
    raise ValueError("unknown scalar")


def predicate_refs(predicate, schema, depth=0):
    if (
        depth > 12
        or not isinstance(predicate, dict)
        or not isinstance(predicate.get("op"), str)
    ):
        raise ValueError("invalid or overly deep predicate")
    op = predicate["op"]
    if op in ("true", "false", "unknown"):
        if set(predicate) != {"op"}:
            raise ValueError("constant predicate has extra fields")
        return set()
    if set(predicate) != {"op", "args"} or not isinstance(predicate["args"], list):
        raise ValueError("predicate must contain op and args")
    args = predicate["args"]
    if op in ("eq", "ne", "lt", "le", "gt", "ge", "is_null"):
        if len(args) != (1 if op == "is_null" else 2):
            raise ValueError("wrong predicate arity")
        types = [scalar_type(arg, schema) for arg in args]
        if len(types) == 2 and None not in types and types[0] != types[1]:
            raise ValueError("mixed-type comparison is outside the model")
        return {arg["column"] for arg in args if "column" in arg}
    if op in ("and", "or", "not"):
        if len(args) != (1 if op == "not" else 2):
            raise ValueError("wrong boolean arity")
        return set().union(*(predicate_refs(arg, schema, depth + 1) for arg in args))
    raise ValueError("unsupported predicate operation")


def scalar_value(scalar, row, indices):
    return row[indices[scalar["column"]]] if "column" in scalar else scalar["literal"]


def evaluate(predicate, row, indices):
    """Return exactly True, False or None (SQL UNKNOWN)."""
    op = predicate["op"]
    if op in ("true", "false", "unknown"):
        return {"true": True, "false": False, "unknown": None}[op]
    args = predicate["args"]
    if op in ("and", "or"):
        left, right = (evaluate(arg, row, indices) for arg in args)
        if op == "and":
            return (
                False
                if left is False or right is False
                else None if left is None or right is None else True
            )
        return (
            True
            if left is True or right is True
            else None if left is None or right is None else False
        )
    if op == "not":
        value = evaluate(args[0], row, indices)
        return None if value is None else not value
    values = [scalar_value(arg, row, indices) for arg in args]
    if op == "is_null":
        return values[0] is None
    if any(value is None for value in values):
        return None
    left, right = values
    return {
        "eq": lambda: left == right,
        "ne": lambda: left != right,
        "lt": lambda: left < right,
        "le": lambda: left <= right,
        "gt": lambda: left > right,
        "ge": lambda: left >= right,
    }[op]()


def validate(plan, tables):
    if (
        not isinstance(tables, dict)
        or any(type(table) is not Table for table in tables.values())
        or not 1 <= len(tables) <= 8
    ):
        raise ValueError("expected named Table objects")
    for name in tables:
        identifier(name)
    schemas = {}
    nodes = 0

    def walk(node, path, depth):
        nonlocal nodes
        nodes += 1
        if (
            nodes > 31
            or depth > 12
            or not isinstance(node, dict)
            or not isinstance(node.get("op"), str)
        ):
            raise ValueError("invalid plan or exceeded 31-node / 12-depth bound")
        op = node["op"]
        if op == "scan":
            if (
                set(node) != {"op", "table"}
                or not isinstance(node["table"], str)
                or node["table"] not in tables
            ):
                raise ValueError("invalid scan")
            schema = tables[node["table"]].columns
        elif op in ("select", "project"):
            required = {"op", "input", "predicate" if op == "select" else "columns"}
            if set(node) != required:
                raise ValueError("invalid unary plan fields")
            child = walk(node["input"], path + ".input", depth + 1)
            if op == "select":
                predicate_refs(node["predicate"], child)
                schema = child
            else:
                columns = node["columns"]
                if (
                    not isinstance(columns, list)
                    or any(not isinstance(name, str) for name in columns)
                    or len(set(columns)) != len(columns)
                    or not set(columns) <= set(dict(child))
                ):
                    raise ValueError("projection needs unique known column names")
                schema = tuple((name, dict(child)[name]) for name in columns)
        elif op == "join":
            if (
                set(node) != {"op", "left", "right", "keys", "kind", "algorithm"}
                or node["kind"] not in ("inner", "left")
                or node["algorithm"] not in ("nested", "hash")
            ):
                raise ValueError("invalid join")
            left = walk(node["left"], path + ".left", depth + 1)
            right = walk(node["right"], path + ".right", depth + 1)
            if set(dict(left)) & set(dict(right)):
                raise ValueError("join sides require disjoint qualified column names")
            keys = node["keys"]
            if not isinstance(keys, list) or not 1 <= len(keys) <= 4:
                raise ValueError("join needs 1..4 equality key pairs")
            for pair in keys:
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or any(not isinstance(name, str) for name in pair)
                ):
                    raise ValueError("invalid join key pair")
                if (
                    pair[0] not in dict(left)
                    or pair[1] not in dict(right)
                    or dict(left)[pair[0]] != dict(right)[pair[1]]
                ):
                    raise ValueError(
                        "join keys must exist on their side and have equal types"
                    )
            schema = left + right
            if len(schema) > 32:
                raise ValueError("intermediate schema exceeds 32 columns")
        else:
            raise ValueError("unsupported plan operation")
        schemas[path] = schema
        return schema

    walk(plan, "root", 0)
    return schemas


def execute(plan, tables, output_limit=MAX_OUTPUT, pair_limit=MAX_PAIRS):
    if (
        type(output_limit) is not int
        or not 1 <= output_limit <= MAX_OUTPUT
        or type(pair_limit) is not int
        or not 1 <= pair_limit <= MAX_PAIRS
    ):
        raise ValueError("invalid execution bounds")
    plan = copy.deepcopy(plan)
    schemas = validate(plan, tables)
    stats = {}

    def iterator(node, path):
        count = {
            "operator": node["op"],
            "input_rows": 0,
            "output_rows": 0,
            "predicate_evaluations": 0,
            "candidate_pairs": 0,
            "null_key_rows": 0,
            "right_materialized": 0,
            "distinct_build_keys": 0,
            "largest_key_group": 0,
        }
        stats[path] = count

        def emit(row):
            count["output_rows"] += 1
            if count["output_rows"] > output_limit:
                raise RuntimeError(
                    "operator output budget exceeded; no complete result"
                )
            return row

        op = node["op"]
        if op == "scan":
            for row in tables[node["table"]].rows:
                yield emit(row)
        elif op == "select":
            indices = {name: index for index, (name, _) in enumerate(schemas[path])}
            for row in iterator(node["input"], path + ".input"):
                count["input_rows"] += 1
                count["predicate_evaluations"] += 1
                if evaluate(node["predicate"], row, indices) is True:
                    yield emit(row)
        elif op == "project":
            indices = {
                name: index for index, (name, _) in enumerate(schemas[path + ".input"])
            }
            selected = [indices[name] for name in node["columns"]]
            for row in iterator(node["input"], path + ".input"):
                count["input_rows"] += 1
                yield emit(tuple(row[index] for index in selected))
        else:
            left_indices = {
                name: index for index, (name, _) in enumerate(schemas[path + ".left"])
            }
            right_indices = {
                name: index for index, (name, _) in enumerate(schemas[path + ".right"])
            }
            left_keys = [left_indices[pair[0]] for pair in node["keys"]]
            right_keys = [right_indices[pair[1]] for pair in node["keys"]]
            right = list(iterator(node["right"], path + ".right"))
            count["right_materialized"] = len(right)
            count["input_rows"] += len(right)
            buckets = defaultdict(list)
            for row in right:
                key = tuple(row[index] for index in right_keys)
                if None in key:
                    count["null_key_rows"] += 1
                elif node["algorithm"] == "hash":
                    buckets[key].append(row)
            count["distinct_build_keys"] = len(buckets)
            count["largest_key_group"] = max(map(len, buckets.values()), default=0)
            for left in iterator(node["left"], path + ".left"):
                count["input_rows"] += 1
                key = tuple(left[index] for index in left_keys)
                if None in key:
                    count["null_key_rows"] += 1
                candidates = (
                    buckets.get(key, [])
                    if node["algorithm"] == "hash" and None not in key
                    else [] if node["algorithm"] == "hash" else right
                )
                matched = False
                for row in candidates:
                    count["candidate_pairs"] += 1
                    if count["candidate_pairs"] > pair_limit:
                        raise RuntimeError(
                            "join comparison budget exceeded; no complete result"
                        )
                    right_key = tuple(row[index] for index in right_keys)
                    if None not in key and None not in right_key and key == right_key:
                        matched = True
                        yield emit(left + row)
                if not matched and node["kind"] == "left":
                    yield emit(left + (None,) * len(schemas[path + ".right"]))

    rows = list(iterator(plan, "root"))
    return {
        "columns": [list(column) for column in schemas["root"]],
        "rows": [list(row) for row in rows],
        "stats": stats,
    }


def optimize(plan, tables):
    """Only safe total-predicate rewrites; outer-join right pushdown is absent."""
    validate(plan, tables)
    changes = []

    def rewrite(node):
        node = copy.deepcopy(node)
        for child in ("input", "left", "right"):
            if child in node:
                node[child] = rewrite(node[child])
        if node["op"] == "select" and node["input"]["op"] == "select":
            lower = node["input"]
            combined = {
                "op": "and",
                "args": [lower["predicate"], node["predicate"]],
            }
            schema = validate(lower["input"], tables)["root"]
            try:
                predicate_refs(combined, schema)
            except ValueError:
                # Valid individual predicates can exceed the depth cap together.
                # Retain the original structure instead of making a valid plan fail.
                pass
            else:
                changes.append("combine_selections")
                node = {"op": "select", "input": lower["input"], "predicate": combined}
        if node["op"] == "project" and node["input"]["op"] == "project":
            changes.append("collapse_projections")
            node["input"] = node["input"]["input"]
        if node["op"] == "select" and node["input"]["op"] == "join":
            join = node["input"]
            schema = validate(join, tables)
            refs = predicate_refs(node["predicate"], schema["root"])
            # Require at least one referenced column to avoid ambiguous constant routing.
            side = None
            if refs and refs <= set(dict(schema["root.left"])):
                side = "left"
            elif (
                join["kind"] == "inner"
                and refs
                and refs <= set(dict(schema["root.right"]))
            ):
                side = "right"
            if side:
                changes.append(f"push_{side}_predicate_through_{join['kind']}_join")
                join[side] = {
                    "op": "select",
                    "input": join[side],
                    "predicate": node["predicate"],
                }
                node = join
        return node

    optimized = rewrite(plan)
    validate(optimized, tables)
    return optimized, changes
