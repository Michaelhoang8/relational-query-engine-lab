"""Independent SQLite semantics oracle; no engine execution/predicate helpers."""

import sqlite3


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def sqlite_result(plan, tables):
    """Compile an already valid explicit plan into nested parameterized SQL.

    Zero-column relations use a private carrier column and discard it at the
    boundary. Empty tuple multiplicity is therefore preserved.
    """
    connection = sqlite3.connect(":memory:")
    parameters = []
    try:
        for name, table in tables.items():
            columns = table.columns or (("__empty__", "integer"),)
            definition = ", ".join(
                quoted(column)
                + " "
                + ("INTEGER" if dtype == "integer" else "TEXT COLLATE BINARY")
                for column, dtype in columns
            )
            connection.execute(f"CREATE TABLE {quoted(name)} ({definition})")
            placeholders = ",".join("?" for _ in columns)
            values = table.rows if table.columns else [(1,) for _ in table.rows]
            connection.executemany(
                f"INSERT INTO {quoted(name)} VALUES ({placeholders})", values
            )

        def scalar(value):
            if "column" in value:
                return quoted(value["column"])
            parameters.append(value["literal"])
            return "?"

        def predicate(value):
            op = value["op"]
            if op in ("true", "false", "unknown"):
                return {"true": "1", "false": "0", "unknown": "NULL"}[op]
            args = value["args"]
            if op == "not":
                return f"(NOT {predicate(args[0])})"
            if op in ("and", "or"):
                return f"({predicate(args[0])} {op.upper()} {predicate(args[1])})"
            if op == "is_null":
                return f"({scalar(args[0])} IS NULL)"
            operator = {
                "eq": "=",
                "ne": "<>",
                "lt": "<",
                "le": "<=",
                "gt": ">",
                "ge": ">=",
            }[op]
            return f"({scalar(args[0])} {operator} {scalar(args[1])})"

        def compile_node(node):
            op = node["op"]
            if op == "scan":
                columns = [column for column, _ in tables[node["table"]].columns]
                selection = (
                    ", ".join(map(quoted, columns)) if columns else '1 AS "__empty__"'
                )
                return f"SELECT {selection} FROM {quoted(node['table'])}", columns
            if op == "select":
                query, columns = compile_node(node["input"])
                return (
                    f"SELECT * FROM ({query}) WHERE {predicate(node['predicate'])}",
                    columns,
                )
            if op == "project":
                query, _ = compile_node(node["input"])
                columns = node["columns"]
                selection = (
                    ", ".join(map(quoted, columns)) if columns else '1 AS "__empty__"'
                )
                return f"SELECT {selection} FROM ({query})", columns
            left, left_columns = compile_node(node["left"])
            right, right_columns = compile_node(node["right"])
            condition = " AND ".join(
                f"l.{quoted(pair[0])} = r.{quoted(pair[1])}" for pair in node["keys"]
            )
            columns = left_columns + right_columns
            selection = ", ".join(
                [
                    *(f"l.{quoted(name)} AS {quoted(name)}" for name in left_columns),
                    *(f"r.{quoted(name)} AS {quoted(name)}" for name in right_columns),
                ]
            )
            kind = "LEFT JOIN" if node["kind"] == "left" else "INNER JOIN"
            return (
                f"SELECT {selection} FROM ({left}) l {kind} ({right}) r ON {condition}",
                columns,
            )

        query, columns = compile_node(plan)
        rows = connection.execute(query, parameters).fetchall()
        if not columns:
            rows = [() for _ in rows]
        return {
            "query": query,
            "parameters": parameters,
            "columns": columns,
            "rows": [list(row) for row in rows],
            "sqlite_version": sqlite3.sqlite_version,
        }
    finally:
        connection.close()
