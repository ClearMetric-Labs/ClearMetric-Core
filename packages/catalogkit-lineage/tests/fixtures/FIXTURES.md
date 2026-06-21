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

### Enterprise adversarial manifest

`adversarial/enterprise_adversarial_manifest` is a single manifest-based bundle
with 15 dbt models plus staging sources, covering enterprise-style traps such as
bare and qualified `SELECT *`, `UNION ALL`, ambiguous joins, self-joins, CTE
shadowing/reuse, scalar and derived-table subqueries, expression fan-out, window
functions, quoted identifiers, rename chains, and dead columns dropped inside CTEs.

It is kept in the adversarial suite rather than `projects/` because it is a
targeted exact-edge oracle for parser robustness, not a vendored production corpus
slice. Assertions use CatalogKit **value-lineage** semantics (`derives_from`), so
reference-only usages such as `CASE` predicates and window partition keys are
expected to warn rather than invent edges.

**Strict contract (R6–R8):** `select_star`, UNION, and quoted/unresolved outputs
warn without emitting `derives_from` for those columns. The independent hand
oracle `value_lineage_expected.yml` must only change when a human re-derives
lineage from SQL — never to match `expected.yaml` or engine output. CI validates
live engine counts via `test_value_lineage_oracle.py`; `expected.yaml` is the
full-edge snapshot for `test_adversarial.py`. See `RECONCILIATION.md` in the
fixture folder for the pinned 70→53 breakdown.

Regenerate `expected.yaml` after engine changes with
`python scripts/refresh_enterprise_adversarial_expected.py`.

### Warning-cause variants

`adversarial/enterprise_warning_variants` holds nine SQL variants (union, window,
quoted) plus minimal staging dependencies. `variant_expected_warnings.yml` drives
`test_variant_warning_categories.py`, which asserts both warning codes **and**
edge suppression (union variants must emit zero `derives_from` edges). This proves
warnings track SQL category, not original `adv_NN` phrasing.

### Micro-fixture roles under strict value-lineage

| Fixture | Role |
|---|---|
| `select_star_with_schema` | Primary **R6** guard — schema-known `SELECT *` → 0 edges + `select_star` |
| `union_all_branches` | **R7** guard — union model → 0 edges + `unresolved_lineage` |
| `select_star_no_schema` | Different path — no schema → `unresolved_star_source`; guards unresolved-star, not schema-known R6 |

## Production manifest contract

Manifest-based fixtures must include enough schema metadata for star-heavy compiled SQL:

- model compiled SQL
- declared column metadata on models when available
- source/seed nodes with column lists for macro-union and external boundaries
- tmp-model column metadata inferred from downstream staging projections when absent upstream

When the contract is incomplete, `catalogkit-lineage` emits explicit `missing_schema_metadata` warnings rather than silently under-resolving lineage.
