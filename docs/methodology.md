# Validation, measurements and evidence

All input tables and workloads are synthetic. Source and documentation are AI-assisted. No commercial workload or production deployment is represented.

## Independent semantic checks

The unit suite compares 360 seeded cases (seed 938172) against real SQLite queries. Each case exercises nested execution, hash execution and the scoped optimizer, comparing Counters of complete row tuples. NULLs and duplicates are retained. Truth tables independently compare every AND/OR/NOT combination with SQLite.

Fixed cases cover duplicate keys, unmatched left rows, NULL keys and values, selective filters, safe left-side pushdown, the unsafe right-side outer-join counterexample, empty projection, empty schema and empty input. Additional tests cover composite keys, Unicode-safe typed values, validation budgets and deliberate distinct integer keys with colliding hashes.

The preserved randomized corpus uses a separate seed, 501862, with 80 cases by default. Each artifact retains typed tables, original/optimized/hash plans, complete output bags, SQLite SQL/parameters and per-operator counters.

An independent review test keeps a valid depth-12 predicate unchanged when conjunction would exceed the bound. Another uses distinct signed integers 0, 2^61−1 and 2(2^61−1) with colliding Python hashes and checks both exact bag output and the expected 16 nested candidates versus 3 hash candidates.

## Benchmarks

Uniform input: left and right each contain keys 0–319 once. The left score is index modulo 80. Filtering score<3 retains 12 left rows.

Skewed input: left and right each contain four keys, eighty rows per key. Left scores remain index modulo 80. Each join has 4×80×80=25,600 duplicate matches before filtering. The twelve selected left rows each match eighty right rows, producing 960 rows.

Each workload is executed using nested and hash joins, with a late filter and with safe left-side filter pushdown. This gives eight plans. Every plan's full result is checked against SQLite and the other equivalent plans before measurement.

One untimed warmup precedes twelve actual calls per configuration. Their order is shuffled with seed 76214. perf_counter_ns measures plan copying, validation, iterator evaluation, counters and final row materialization. SQLite oracle runs, JSON encoding, file writes and plotting are outside the measured interval.

This measures the instrumented Python implementation on one machine. It is not an optimized database benchmark, an internal Python hash-table profile, or a performance comparison against SQLite. Skew and output multiplicity are deliberately preserved rather than filtered out of the timing result.

## Reproduction and provenance

```bash
python scripts/verify_results.py
python scripts/run_study.py --output-dir build/reproduction
python scripts/verify_results.py --output-dir build/reproduction
```

The verifier checks source and artifact hashes, reruns all saved engine plans, reissues SQLite queries, compares exact bags, checks optimizer outputs and derives expected candidate counts independently from the benchmark construction.

Generated JSON, CSV, HTML, SVG and logs use LF before hashing. Source digests normalize CRLF to LF. Optional capture tooling and tests/test_media.py are outside measured-source identity; screenshots and recordings can be attached without replacing original measurements. Hashes establish artifact consistency, not authorship or authenticated timestamps.

Fresh runs preserve exact result bags and operator counters; wall-clock timings, UTC timestamps and test durations vary. No ordering is asserted without ORDER BY, which this model does not implement.

CI verifies the preserved study, then creates an independent fresh study under ignored build/ci. Committed evidence is not overwritten by the normal CI path.
