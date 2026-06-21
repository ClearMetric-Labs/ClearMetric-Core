# catalogkit-lineage fixtures

This directory is the single canonical fixture root for `catalogkit-lineage`.

## Provenance

| Fixture | Source repo | Commit SHA | Dialect | Notes |
|---|---|---|---|---|
| `projects/jaffle_shop` | `dbt-labs/jaffle-shop-classic` | `unknown` | `postgres` | Pre-existing vendored slice moved from `examples/jaffle_shop`; contains 5 models and 3 seeds. |
| `projects/sql_folder` | `n/a` | `n/a` | `postgres` | In-repo plain SQL fixture moved from `examples/sql_folder`; contains 3 SQL files. |
| `projects/shopify` | `fivetran/dbt_shopify` | `a970f76a01fff8524540714f9255af8e62c3b985` | `postgres` | Transitive model closure from anchor models in published `docs/manifest.json`; compiled SQL rewritten to local relation names; macro-union source nodes and tmp-model column metadata inferred from staging `fields` CTE projections. |
| `projects/loom_finance` | `p-munhoz/dbt-loom-multi-project-demo` | `fddd6600448f13e8b945958630a1ec0abe959bc3` | `duckdb` | Curated manifest for the finance project using the public cross-project customer dimension and local `orders` seed. |
| `projects/loom_marketing` | `p-munhoz/dbt-loom-multi-project-demo` | `fddd6600448f13e8b945958630a1ec0abe959bc3` | `duckdb` | Curated manifest for the marketing project using the public cross-project customer dimension and local `campaign_events` seed. |

## Adversarial micro-fixtures

The `adversarial/*` cases use production-shaped compiled SQL patterns (Fivetran-style
`base`/`fields`/`final` staging, `table.*` join projections, dbt ephemeral CTE
wrappers, surrogate-key expressions) distilled into minimal manifests. Each case
locks exact `derives_from` edges and warning codes via `expected.yaml`.

## Production manifest contract

Manifest-based fixtures must include enough schema metadata for star-heavy compiled SQL:

- model compiled SQL
- declared column metadata on models when available
- source/seed nodes with column lists for macro-union and external boundaries
- tmp-model column metadata inferred from downstream staging projections when absent upstream

When the contract is incomplete, `catalogkit-lineage` emits explicit `missing_schema_metadata` warnings rather than silently under-resolving lineage.
