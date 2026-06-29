# Derived state and confidence

Each column node can carry **derivation** metadata:

- `status` — e.g. complete vs unresolved
- `confidence` — how strongly the resolver trusts the edge
- `source` — e.g. sqlglot-backed lineage
- `errors` — structured failure detail when incomplete

When SQL is valid but messy (`SELECT *`, ambiguous joins, union branches), ClearMetric emits
**warnings** on the artifact rather than failing the whole compile — unless input is structurally broken.

Impact traversal uses **value lineage** (`derives_from`): columns whose values flow into an output.
Predicate-only references (WHERE, JOIN ON, etc.) are out of scope for impact today.

Resolver behavior and SQL pattern limits: [SQL support and limits](../validation/sql-limits.md).

Contract fields: [Artifact contract](../reference/contract.md).
