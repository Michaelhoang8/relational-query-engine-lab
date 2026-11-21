# Optimizer rules and limits

## Combine selections

For pure, total predicates p and q, retaining a row first under q then under p is equivalent to retaining it under q AND p. In three-valued logic, the conjunction is TRUE exactly when both terms are TRUE. UNKNOWN does not make the two-stage filter differ.

Combining introduces one extra predicate-tree level. The optimizer skips this rewrite if it would exceed the supported predicate-depth bound, keeping the already valid selection structure.

This reasoning would need revisiting for arithmetic errors, side effects, volatile functions or short-circuit-dependent behavior. None is part of this predicate language.

## Collapse projections

Projection A over projection B can become projection A over the original input when A is a subset of B, with names unique and preserved. Validation establishes the subset relationship. This rewrite does not deduplicate rows: every original row contributes once, even when A is empty.

## Push side-local predicates

For an inner equijoin, a predicate referencing only one input can be evaluated on that input before joining. Every output pair gets the same predicate truth value, and multiplicities are unchanged.

For a left join, a left-only predicate can also move into the left input. It either preserves or removes every match family for that left row, including its NULL-extended row when there is no match.

The implementation only routes nonempty column-reference sets and does not split arbitrary conjunctions across sides. Right-only predicates can pass through inner joins but **not** left joins. It performs no join reordering or cost-based search. Hash versus nested execution is evaluated as a separate physical choice.

## Counterexample

Original plan: left-join all rows on l.key=r.key, then retain only r.score>10.

The right table contains scores 8 and 12 for key 1. Two identical key-1 left rows each retain the score-12 match, producing two rows. Other left rows have scores at most 10, a NULL score, a NULL key, or no match. Their post-join predicate is FALSE or UNKNOWN.

Unsafe proposed rewrite: filter the right input to score>10, left-join it, and remove the outer filter.

Only the right key-1 score-12 row remains. The two matching left duplicates still produce two rows, but five other left rows now produce NULL-extended output. Total: seven. Both plans match their own independent SQLite query; their bags differ.

The absence of this rewrite is intentional. Potential alternatives involving retaining a post-join predicate or changing an outer join to an inner join require additional proved preconditions and are not implemented.
