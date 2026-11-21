"""Plan diagrams, exact counterexample tables, and plots from retained results."""

from collections import Counter
import csv
import html
import json
from pathlib import Path
import statistics

PAPER = "#f7f5ee"
INK = "#202f33"
ACCENT = "#9b472f"
MUTED = "#718080"
GREEN = "#3f746a"


def escape(value):
    return html.escape(str(value))


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def svg(width, height, title):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        f'<rect width="100%" height="100%" fill="{PAPER}"/>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#80908c"/></marker></defs>',
        "<style>text{font-family:Arial,sans-serif;font-size:14px}.small{font-size:12px}.mono{font-family:monospace;font-size:13px}</style>",
    ]


def text(x, y, value, color=INK, anchor="middle", css=""):
    return f'<text x="{x}" y="{y}" fill="{color}" text-anchor="{anchor}" class="{css}">{escape(value)}</text>'


def plan_diagram(before, after):
    parts = svg(
        1140, 490, "Actual operator row flow before and after safe predicate pushdown"
    )
    for panel, record in enumerate((before, after)):
        offset = panel * 570
        parts.append(
            text(
                offset + 285,
                28,
                "Filter after join" if panel == 0 else "Filter before join",
                ACCENT,
            )
        )
        if panel == 0:
            positions = {
                "root": (285, 100),
                "root.input": (285, 245),
                "root.input.left": (140, 385),
                "root.input.right": (430, 385),
            }
            labels = {
                "root": "Select l.score < 3",
                "root.input": "Nested inner join",
                "root.input.left": "Scan left",
                "root.input.right": "Scan right",
            }
            edges = [
                ("root.input", "root"),
                ("root.input.left", "root.input"),
                ("root.input.right", "root.input"),
            ]
        else:
            positions = {
                "root": (285, 100),
                "root.left": (140, 245),
                "root.right": (430, 245),
                "root.left.input": (140, 385),
            }
            labels = {
                "root": "Nested inner join",
                "root.left": "Select l.score < 3",
                "root.right": "Scan right",
                "root.left.input": "Scan left",
            }
            edges = [
                ("root.left", "root"),
                ("root.right", "root"),
                ("root.left.input", "root.left"),
            ]
        stats = record["result"]["stats"]
        for child, parent in edges:
            x1, y1 = positions[child]
            x2, y2 = positions[parent]
            parts.append(
                f'<path d="M{x1+offset} {y1-29} L{x2+offset} {y2+34}" stroke="#80908c" fill="none" stroke-width="1.6" marker-end="url(#arrow)"/>'
            )
            parts.append(
                text(
                    (x1 + x2) / 2 + offset + 24,
                    (y1 + y2) / 2,
                    f'{stats[child]["output_rows"]} rows',
                    MUTED,
                    css="small",
                )
            )
        for path, (x, y) in positions.items():
            width = 220
            parts.append(
                f'<rect x="{x+offset-width/2}" y="{y-29}" width="{width}" height="62" fill="#eeeae0" stroke="#b8c1b9" rx="3"/>'
            )
            parts.append(text(x + offset, y - 4, labels[path]))
            parts.append(
                text(
                    x + offset,
                    y + 18,
                    f'output {stats[path]["output_rows"]}',
                    GREEN,
                    css="mono",
                )
            )
        pairs = sum(row["candidate_pairs"] for row in stats.values())
        parts.append(
            text(
                offset + 285,
                470,
                f"{pairs:,} candidate pairs · 12 final rows",
                ACCENT,
                css="mono",
            )
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def measurements(summary, timings):
    parts = svg(1140, 490, "Exact join candidate counts and measured local durations")
    labels = [row["configuration"] for row in summary]
    max_time = max(int(row["elapsed_ns"]) / 1e6 for row in timings) * 1.05
    left, span = 390, 660
    for tick in range(5):
        x = left + tick * span / 4
        parts.append(f'<path d="M{x} 30V420" stroke="#dce0d7"/>')
        parts.append(text(x, 446, f"{max_time*tick/4:.1f}", MUTED, css="small"))
    for index, row in enumerate(summary):
        y = 50 + index * 48
        label = row["configuration"]
        values = [
            int(item["elapsed_ns"]) / 1e6
            for item in timings
            if item["configuration"] == label
        ]
        color = GREEN if "_hash_" in label else ACCENT
        parts.append(text(15, y + 4, label.replace("_", " "), INK, "start", "small"))
        parts.append(
            text(365, y + 4, f'{row["candidate_pairs"]:,}', color, "end", "mono")
        )
        for j, value in enumerate(values):
            parts.append(
                f'<circle cx="{left+value/max_time*span}" cy="{y+(j%5-2)*2}" r="3" fill="{color}" opacity=".55"/>'
            )
        median = statistics.median(values)
        parts.append(
            f'<path d="M{left+median/max_time*span} {y-10}V{y+10}" stroke="{INK}" stroke-width="2"/>'
        )
    parts.append(text(365, 20, "PAIRS", MUTED, "end", "small"))
    parts.append(
        text(
            720,
            480,
            "Elapsed milliseconds · dots: actual samples · vertical tick: median",
            MUTED,
            css="small",
        )
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def table_rows(rows):
    output = ""
    for row in rows:
        output += (
            "<tr>"
            + "".join(
                (
                    '<td class="null">NULL</td>'
                    if value is None
                    else f"<td>{escape(value)}</td>"
                )
                for value in row
            )
            + "</tr>"
        )
    return output


def generate(out):
    def read(path):
        return json.loads((out / path).read_text(encoding="utf-8"))

    before = read("results/benchmarks/uniform_nested_late_filter.json")
    after = read("results/benchmarks/uniform_nested_pushdown.json")
    benchmark = read("results/benchmark-summary.json")
    environment = read("results/environment.json")
    with (out / "results/timings.csv").open(newline="", encoding="utf-8") as handle:
        timings = list(csv.DictReader(handle))
    write(out / "docs/figures/plans.svg", plan_diagram(before, after))
    write(out / "docs/figures/measurements.svg", measurements(benchmark, timings))
    original = read("results/cases/outer_where.json")["original"]["rows"]
    unsafe = read("results/cases/unsafe_right_pushdown.json")["original"]["rows"]
    duplicated = read("results/cases/duplicate_inner.json")["original"]["rows"]
    bag_rows = [
        list(row) + [count] for row, count in Counter(map(tuple, duplicated)).items()
    ]
    from relab.engine import evaluate

    truth_rows = []
    for first in ("true", "false", "unknown"):
        for second in ("true", "false", "unknown"):
            values = []
            for op in ("and", "or"):
                value = evaluate(
                    {"op": op, "args": [{"op": first}, {"op": second}]}, (), {}
                )
                values.append("UNKNOWN" if value is None else str(value).upper())
            truth_rows.append([first.upper(), second.upper(), *values])
    rendered = TEMPLATE.replace("{{ORIGINAL}}", table_rows(original)).replace(
        "{{UNSAFE}}", table_rows(unsafe)
    )
    rendered = rendered.replace("{{BAGS}}", table_rows(bag_rows)).replace(
        "{{TRUTH}}", table_rows(truth_rows)
    )
    rendered = rendered.replace("{{RANDOM}}", str(environment["random_cases"])).replace(
        "{{SAMPLES}}", str(environment["samples_per_configuration"])
    )
    rendered = rendered.replace("{{SQLITE}}", environment["sqlite_version"])
    write(out / "docs/report.html", rendered)


TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Relational execution · plans and semantics</title><style>
*{box-sizing:border-box}body{margin:0;background:#f7f5ee;color:#202f33;font:16px/1.55 system-ui,sans-serif}main{max-width:1250px;margin:auto;padding:0 42px}nav{padding:22px 0;border-bottom:1px solid #bec6bd;display:flex;gap:22px;flex-wrap:wrap;font:12px ui-monospace,monospace}a{color:#3f746a}nav a{text-decoration:none}section{padding:38px 0 46px;border-bottom:1px solid #bec6bd}.header{display:grid;grid-template-columns:1fr 1fr;gap:60px;align-items:center}.header>*{min-width:0}h1{font-size:clamp(35px,4.5vw,58px);font-weight:500;line-height:1.08;letter-spacing:-1.8px;margin:30px 0 18px}h2{font-size:28px;line-height:1.2;font-weight:500;letter-spacing:-.5px;margin:0 0 22px}h3{font-size:17px;font-weight:600;margin:0 0 10px}.kicker{font:12px ui-monospace,monospace;letter-spacing:1px;color:#718080;margin-top:26px}p{max-width:920px}.two{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:44px}.two>*{min-width:0}pre{background:#eeeae0;padding:20px;font:13px/1.8 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}code{overflow-wrap:anywhere}figure{margin:20px 0}img{display:block;width:100%;height:auto}figcaption{font-size:13px;color:#718080;margin-top:10px}.scroll{max-width:100%;overflow:auto}.scroll:focus-visible{outline:2px solid #9b472f;outline-offset:3px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:11px;border-bottom:1px solid #d7dcd2}th{font-weight:500}thead th{font:11px ui-monospace,monospace;color:#718080;text-transform:uppercase}.null{color:#9b472f;font-family:ui-monospace,monospace}.note{border-left:2px solid #9b472f;padding-left:18px}.metric{font:24px ui-monospace,monospace;color:#9b472f;padding:20px 0}.links{display:flex;gap:20px;flex-wrap:wrap;font-size:13px;margin-top:24px}footer{font-size:12px;color:#718080;padding:24px 0 40px}@media(max-width:700px){main{padding:0 18px}.header,.two{grid-template-columns:minmax(0,1fr);gap:20px}h1{letter-spacing:-1px}h2{font-size:24px}.scroll table{min-width:430px}nav{gap:14px}section{padding:30px 0}}
</style></head><body><main>
<section id="overview"><nav><a href="#overview">Execution</a><a href="#plans">Rewrites</a><a href="#nulls">NULL / bags</a><a href="#outerjoin">Outer join</a><a href="#joins">Join costs</a><a href="#reproduction">Verification</a></nav><div class="header"><div><div class="kicker">DATABASE EXECUTION / TYPED RELATIONAL OPERATORS</div><h1>Relational query<br>plans and row flow</h1><p>An iterator engine for selections, projections and equijoins, with exact duplicate and NULL semantics checked by local SQLite queries.</p></div><pre>SELECT l."l.key", l."l.score",
       r."r.key", r."r.score"
FROM "left" AS l
JOIN "right" AS r
  ON l."l.key" = r."r.key"
WHERE l."l.score" &lt; 3

Inputs: 320 rows on each side
Result: 12 rows</pre></div><figure><img src="figures/plans.svg" alt="Actual operator diagrams before and after left-side filter pushdown"><figcaption>Actual uniform-workload counters. Arrows show row flow. Both nested-loop plans return the same bag; early filtering reduces candidate comparisons from 102,400 to 3,840.</figcaption></figure></section>
<section id="plans"><h2>Scoped optimizer rewrites</h2><div class="two"><div><h3>Implemented rules</h3><pre>Select(p, Select(q, R))
  → Select(q AND p, R)

Project(A, Project(B, R))
  → Project(A, R), where A ⊆ B

Select(left_only, Join(L, R))
  → Join(Select(left_only, L), R)</pre><p>Left-only predicates may pass through inner and left joins. Right-only predicates may pass through inner joins. Columns retain unique names, and every predicate in this restricted language is pure and total.</p></div><div><h3>Explicit limits</h3><p>The engine does not parse general SQL or claim universal relational rewrites. There is no right-side filter pushdown through a left join, no join reordering, no expression evaluation with side effects, and no DISTINCT operator.</p><p class="note">Physical join choice is a separate comparison. Switching nested-loop to hash equijoin preserves the output bag while changing work and memory usage. Output ordering is not part of this model.</p><div class="metric">102,400 → 3,840 pairs</div><p>Same two 320-row inputs; twelve left rows pass the selective predicate. This count follows directly from 12 × 320 candidate comparisons.</p></div></div></section>
<section id="nulls"><h2>Three-valued logic and bag multiplicity</h2><div class="two"><div><h3>SQL boolean truth table</h3><div class="scroll" tabindex="0" role="region" aria-label="Scrollable NULL truth table"><table><thead><tr><th>p</th><th>q</th><th>p AND q</th><th>p OR q</th></tr></thead><tbody>{{TRUTH}}</tbody></table></div><p>Selection retains only TRUE. FALSE and UNKNOWN both discard a row. Ordinary equality with NULL is UNKNOWN; two NULL join keys do not match.</p></div><div><h3>Actual inner-join output groups</h3><div class="scroll" tabindex="0" role="region" aria-label="Scrollable duplicate row groups"><table><thead><tr><th>l.key</th><th>l.value</th><th>r.key</th><th>r.score</th><th>Count</th></tr></thead><tbody>{{BAGS}}</tbody></table></div><p>Two identical left rows each match both key-1 right rows. Projection retains these duplicates. Even an empty column projection emits one empty tuple per input row.</p><p class="note">Integer keys −1 and −2 deliberately share a Python hash. Equality still distinguishes them; hash collisions do not create false joins.</p></div></div></section>
<section id="outerjoin"><h2>Unsafe outer-join pushdown: a counterexample</h2><p>Moving <code>WHERE r.score &gt; 10</code> into the right input of a left join introduces new NULL-extended rows. Each version below is independently checked against its corresponding SQLite query. They are intentionally different plans.</p><div class="two"><div><h3>Original: filter after left join · 2 rows</h3><pre>WHERE r.score &gt; 10
  LEFT JOIN ON l.key = r.key</pre><div class="scroll" tabindex="0" role="region" aria-label="Original outer join result"><table><thead><tr><th>l.key</th><th>l.value</th><th>r.key</th><th>r.score</th></tr></thead><tbody>{{ORIGINAL}}</tbody></table></div><p>NULL-extended rows fail the WHERE predicate because the comparison is UNKNOWN.</p></div><div><h3>Unsafe rewrite: filter right input · 7 rows</h3><pre>LEFT JOIN ON l.key = r.key
  RIGHT INPUT WHERE r.score &gt; 10</pre><div class="scroll" tabindex="0" role="region" aria-label="Unsafe pushed-down outer join result"><table><thead><tr><th>l.key</th><th>l.value</th><th>r.key</th><th>r.score</th></tr></thead><tbody>{{UNSAFE}}</tbody></table></div></div></div><p class="note">The optimizer refuses this transformation. A different rewrite, such as retaining a suitable post-join filter or changing join type under proven conditions, would require its own rule and proof.</p></section>
<section id="joins"><h2>Join algorithms, selectivity and skew</h2><p>Nested-loop joins examine every candidate pair. Hash joins build keyed groups from the right side and probe only equal non-NULL keys. Both retain duplicate matches and NULL-extend an unmatched left row once.</p><figure><img src="figures/measurements.svg" alt="Actual candidate-pair counts and local timing samples for uniform and skewed workloads"><figcaption>{{SAMPLES}} actual calls per configuration, after one warmup and in a seeded interleaved order. Counts are exact instrumented candidates; elapsed times include validation, counters and final result materialization.</figcaption></figure><div class="two"><p>The uniform hash case examines 320 pairs, or 12 after pushdown. The skewed case has four keys with 80 rows per side per key: 25,600 matches before filtering, or 960 after filtering the left input.</p><p class="note">A hash table cannot eliminate required duplicate output. Both algorithms materialize the right input; hash joins additionally store keyed groups. These small local runs are not a comparison against SQLite's performance or a production database benchmark.</p></div></section>
<section id="reproduction"><h2>Independent semantics checks and reproduction</h2><p>SQLite {{SQLITE}} executes local queries with parameterized inserts and predicate literals. Results are compared as multisets, including duplicates and NULLs. No ordering is asserted because no ORDER BY operator is implemented.</p><div class="two"><div><h3>Retained evidence</h3><p>Nine fixed cases, {{RANDOM}} preserved randomized cases, eight benchmark plans, typed inputs, original/optimized plans, full outputs, SQLite SQL/parameters and operator counters are retained.</p><p>The unit suite separately checks 360 seeded plans, complete three-valued boolean tables, composite keys, empty schemas, empty inputs, duplicate rows, deliberate hash collisions and execution limits.</p></div><pre>python -m unittest discover -s tests -v
python -m relab examples/selective_inner.json \
  --optimize --sqlite-check
python scripts/verify_results.py
python scripts/run_study.py \
  --output-dir build/reproduction</pre></div><p>Inputs support signed 64-bit integers, bounded UTF-8 text and NULL. Mixed-type comparisons are rejected. Plans, row counts and candidate comparisons are bounded; exceeding a budget raises an error rather than returning a partial complete result.</p><div class="links"><a href="../results/summary.json">Fixed cases</a><a href="../results/benchmark-summary.json">Operator counts</a><a href="../results/timings.csv">Timing samples</a><a href="../results/provenance.json">Provenance</a><a href="semantics.md">Semantics</a><a href="optimizer.md">Rewrite reasoning</a></div></section>
<footer>Synthetic database-execution exercise · AI-assisted code and documentation · Python standard library · SQLite used only as a semantics oracle.</footer>
</main></body></html>"""
