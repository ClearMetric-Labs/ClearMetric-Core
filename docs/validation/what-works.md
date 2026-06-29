# What works today

ClearMetric Core is early (0.x), but several paths are **reliable enough to build on** if you
validate on your SQL and read warnings.

## You can rely on

**Local compile pipeline** — warehouse JSON + dbt manifest + SQL folder → one enforced graph on
your machine. No live warehouse required.

**Column impact on parseable SQL** — upstream/downstream traversal over `derives_from` edges when
the resolver completes derivation. The [lineage demo](../getting-started/five-minute-demo.md)
shows downstream from `orders_base.amount` and upstream to
`customers_report.customer_lifetime_value`.

**Explicit warnings** — unresolved stars, ambiguous joins, and warehouse bind gaps surface as
warnings with codes (e.g. `warehouse_bind_unresolved`), not silent wrong lineage.

**Contract enforcement** — `cm contract` validates artifact schema and security floor for CI.

**Open outputs** — same graph as JSON, catalog slice, OpenLineage event, impact JSON/text/mermaid.

## Honest limits

- Resolver quality depends on SQL shape; messy production SQL may stay partially unresolved.
- Warehouse binding needs a metadata export that matches your models; name mismatches warn.
- Predicate-only column use does not create impact edges today.
- Live connectors, query serving, and experimental formats are out of public scope — see
  [What it does today](../current-scope.md).

## Next steps

1. [Check lineage yourself](check-lineage.md) on a column you know.
2. Read [SQL support and limits](sql-limits.md) before trusting edge cases.
3. [Help test on real projects](help-test.md) — the main feedback loop for resolver quality.
