# Types, operators and execution contract

## Tables and scalars

Table schemas are ordered tuples of unique names and type names (`integer` or `text`). Names contain 1–32 ASCII letters/digits/underscore/dot and begin with a letter. Joined inputs need disjoint qualified names. Each input has at most 16 columns and 1,024 rows. Empty schemas and empty rows are valid.

Integer values are exact signed 64-bit Python integers; booleans are rejected. Text values have at most 256 Unicode characters, must encode as UTF-8, and cannot contain NUL or lone surrogates. NULL is represented by Python None / JSON null and is allowed in every column. Floats, bytes, nested values and mixed declared types are unsupported.

Text comparisons use binary, case-sensitive ordering. No locale collation, numeric-string coercion or SQLite affinity conversion is emulated. Static validation rejects comparisons and join keys of different non-NULL types.

## Predicates

Scalar nodes are `{"column":"l.key"}` or `{"literal":1}`. Predicates use `{"op":"eq","args":[scalar,scalar]}`, with eq/ne/lt/le/gt/ge comparisons; unary is_null; binary and/or; unary not; and zero-argument true/false/unknown constants. Predicates are bounded to depth 12.

Comparisons with either scalar NULL produce UNKNOWN. FALSE AND UNKNOWN is FALSE; TRUE OR UNKNOWN is TRUE; NOT UNKNOWN is UNKNOWN. Selection keeps exactly TRUE. Predicates are pure and total within their validated types, with no division, functions, side effects or error-producing expressions.

## Plans

```json
{
  "op": "select",
  "predicate": {"op": "lt", "args": [{"column": "l.score"}, {"literal": 3}]},
  "input": {
    "op": "join",
    "kind": "inner",
    "algorithm": "hash",
    "keys": [["l.key", "r.key"]],
    "left": {"op": "scan", "table": "left"},
    "right": {"op": "scan", "table": "right"}
  }
}
```

Input files contain exactly `tables` and `plan` fields. Tables use arrays `columns: [[name,type],...]` and `rows: [[value,...],...]`. Every plan/operator node rejects unknown fields.

Scan yields input rows. Select retains matching rows. Project chooses a list of unique existing names, preserving input multiplicity. Its empty column list emits empty tuples without applying DISTINCT. Joins accept 1–4 equality key pairs, each oriented left-column/right-column; output schema is left followed by right.

Inner join emits all matching pairs, including Cartesian duplicate multiplicity within a key group. Left join also emits every unmatched left row once with a NULL value for each right column. NULL in any key component prevents a match, even against another NULL key.

No ORDER BY exists; output order is unspecified. The current implementations often happen to agree on order, but tests and oracle checks assert only multisets.

## Iterators, materialization and budgets

Each operator is a generator consumed by its parent. Both join algorithms materialize their right input to support repeated probes. Hash join additionally stores lists of right rows indexed by their full key tuple; actual equality distinguishes hash collisions. The final execute API collects all output rows into a list.

Validation allows at most 31 nodes, depth 12 and 32 intermediate columns. Output budget applies separately to each operator (default/cap 100,000 rows); candidate budget applies separately to each join (default/cap 2,000,000 pairs). A failure aborts result construction. These count limits are not a byte-level memory cap, security sandbox or wall-clock timeout.

## Counter definitions

| Counter | Meaning |
|---|---|
| input_rows | Rows consumed directly from children; a join counts both sides once |
| output_rows | Rows emitted by that operator, before its parent filters them |
| predicate_evaluations | Whole predicate evaluations at select operators |
| candidate_pairs | Pairs tested by a join; all nested pairs or entries in a matched hash-key group |
| null_key_rows | Left plus right input rows containing at least one NULL key component |
| right_materialized | Rows retained from the right child |
| distinct_build_keys | Number of non-NULL logical key groups in hash execution; zero for nested |
| largest_key_group | Largest logical hash-key row list; not an internal CPython hash bucket |

Candidate counters do not measure CPython's internal hash probes or every primitive scalar comparison. Aggregated operator output counts may count a row at multiple pipeline stages.

## SQLite oracle

The oracle creates a private in-memory SQLite database, inserts validated values through placeholders and emits nested SELECT statements. Literal predicates also use placeholders. Identifiers are quoted. Zero-column relations use a private carrier column removed at the result boundary, preserving the number of empty tuples.

The oracle uses SQLite only for semantics, not engine execution or optimization. Its SQL is retained in each result artifact. The primary references are SQLite's [expression semantics](https://www.sqlite.org/lang_expr.html) and [SELECT / outer-join processing](https://www.sqlite.org/lang_select.html).
