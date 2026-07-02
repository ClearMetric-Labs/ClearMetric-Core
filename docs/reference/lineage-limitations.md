# clearmetric.lineage limitations

`clearmetric.lineage` is intentionally deterministic and headless.

## Corpus and resolver correctness (ongoing)

Resolver correctness is validated by adversarial fixtures under `tests/fixtures/lineage/`,
oracle tests (`test_value_lineage_oracle.py`), ground-truth probes (`ground_truth.py`),
and committed seed fixtures (`tests/fixtures/lineage/seed/`).

Optional hand-traced regression against real dbt projects lives in a **private gitignored
corpus** (see synthetic layout in `corpus.example/cases/example/`). Set
`CLEARMETRIC_CORPUS_ROOT` to point at that checkout when running the private harness locally.
Public CI does not depend on corpus data or comparator reports.

DataHub comparison in the private harness is a **comparator only** — it finds candidate
disagreements but shares sqlglot blind spots and is not ground truth. Scoped residual
measurement, checkpoint resume, and projected recall live in the private harness
(`scripts/_corpus_measurement.py`, invoked via `scripts/corpus_external.py residual-overlap`).
Comparator buckets
(`both`, `datahub_only`, …) require **exact whole-model edge-set equality**; per-model
`overlap` blocks distinguish normalization mismatch from genuine semantic disagreement.

### Value-lineage definition (`derives_from`)

`derives_from` is **value-lineage**: an upstream column’s value must flow into the
downstream column’s value in the selected expression. Predicate-only references are
**excluded** unless their value contributes to the output:

| Included | Excluded (comparator noise when DataHub reports them) |
|---|---|
| Direct projections, casts, arithmetic on column values | `JOIN … ON` keys used only as predicates |
| `CASE` result arms sourcing column values | `WHERE` / `HAVING` filter columns |
| Aggregates over value expressions (`SUM(col)`, …) | Window `PARTITION BY` / `ORDER BY` keys |
| CTE re-projections of value expressions | Filter-only `CASE WHEN` condition columns |

DataHub edges for predicate-only references are **comparator differences**, not CM
correctness failures. Hand-traced `lineage_truth` cases in a private corpus are the
correctness oracle for external projects; comparator reports measure agreement only.

Expression lineage follows the same value-lineage rule:

- literal-only expressions such as `CAST('x' AS TEXT)` do not emit upstream
  `derives_from` edges;
- `COALESCE`, string concatenation, hashes, and arithmetic emit component edges
  only for columns whose values contribute to the output value;
- CTE aliases are transparent passthroughs when the aliased expression itself is
  value-lineage-resolvable.

These are CM semantics, not attempts to mirror DataHub's expression choices.

### Typed schema and precedence

Column types flow through `clearmetric.lineage.schema` with explicit precedence:

1. warehouse `INFORMATION_SCHEMA` export (physical truth when bound)
2. dbt `catalog.json` when present (requires `dbt docs generate`, not plain `dbt compile`)
3. manifest `data_type` (author declaration, often stale or blank)

Conflicts emit `type_conflict` warnings. Warehouse precedence assumes the export and
compiled manifest describe compatible snapshots; a stale warehouse export can win over
branch SQL with a warning but still reflect deployed rather than in-flight schema.

Missing types emit `missing_column_type`; unmapped non-empty types emit
`unsupported_column_type`. Both exclude columns from the sqlglot schema and mark
lineage partial rather than inventing `"text"` placeholders.

Intermediate model output schemas propagate downstream in topological build order.
Cyclic model subgraphs emit `dependency_cycle` warnings and skip sqlglot resolution
for the cyclic remainder without aborting the whole compile.

### Metrics definitions (public fixtures)

**Primary (hand-traced correctness oracle on committed fixtures):**

- `edge_recall = |cm ∩ expected| / |expected|`
- `edge_precision = |cm ∩ expected| / |cm|`
- `exact_model_match` — models where produced edges match expected and no `must_not_edges` appear
- `unsupported_rate`, `parse_failure_rate`

**Secondary (private comparator only — not correctness):**

- `datahub_agreement = |cm ∩ dh| / |cm ∪ dh|` per model (`overlap.jaccard`)
- Whole-model bucket counts (`both`, `datahub_only`, …) — strict set equality, not recall

Do **not** treat comparator bucket counts as universal correctness proof.

### Known unsupported / honest partial areas

| Area | Behavior |
|---|---|
| Dynamic SQL / invisible macros | partial or failed with warnings |
| Unmapped warehouse types | `unsupported_column_type`, column excluded from schema |
| Multi-relation bare `SELECT *` | unsupported unless relation unambiguous |
| Schema-resolved single-relation `SELECT *` | expands when typed columns cover declared outputs |
| UNION / quoted outputs | strict R7/R8 — warnings, no invented edges |
| Dependency cycles | `dependency_cycle`, partial, acyclic portion still resolves |
| Predicate-only references | excluded from value-lineage by design |
| Spellbook without warehouse schema | schema-gated; comparator-only |

When coverage changes, update this document honestly.

## Supported Input

`clearmetric.lineage` accepts exactly one project input per invocation:

- a dbt `manifest.json` file
- a folder containing one or more `.sql` files

The tool does not run dbt. Manifest input must already point at compiled SQL.

## Guarantees

`clearmetric.lineage` guarantees:

- one supported project input per invocation
- dialect-aware lineage tracing through `sqlglot`
- deterministic table and column IDs through `clearmetric.lineage`
- project-level upstream and downstream traversal
- a mergeable `CatalogArtifact`
- loud failure on unsupported invocation shapes

## Current Boundaries

`clearmetric.lineage` does **not** currently provide:

- warehouse execution
- dbt compile / parse orchestration
- connector credentials or live metadata hydration
- intermediate CTE column nodes in the public artifact
- a full OpenLineage event emitter

## Validated dialects

The current committed test corpus exercises:

- `postgres`
- `duckdb`
- `snowflake`
- `bigquery`

The deepest real-project **correctness** coverage today is Tuva on `duckdb` (hand-traced
`lineage_truth` cases) plus adversarial fixtures on `postgres`. The `snowflake` and
`bigquery` checks are adversarial dialect fixtures, not large real-project validations.

Folder input is intentionally lighter-weight than dbt manifest input. When a SQL
folder does not provide root-table schema metadata, `SELECT *` leaves at the
external boundary may remain unresolved and will emit warnings instead of
invented column lineage.

## Warning-Based Behavior

`clearmetric.lineage` warns instead of failing when the project input is valid but
individual SQL files remain messy:

- `SELECT *`
- relation aliases that do not resolve back to a concrete upstream dataset
- unresolved lineage leaves
- per-dataset lineage resolution failures

That recoverable behavior applies inside supported manifest/folder inputs. It
does not relax the top-level input contract.

### Current warning behavior

- `select_star`: emitted when `SELECT *` or `table.*` is present
- `unresolved_star_source`: emitted when lineage leaf or output expansion stays at `*`
- `unresolved_output_source`: emitted when lineage stops at a relation alias or otherwise cannot identify a concrete upstream dataset for an output column
- `unresolved_lineage`: emitted when a local model column remains unresolved after the full build and reconciliation pass
- `lineage_resolution_failed`: emitted when an individual dataset cannot be parsed or resolved, while sibling datasets continue

Column-scoped warnings now carry a canonical `subject_id` such as
`column:shopify__customers.lifetime_total_spent`. That is the trust model:
every local-model column is either resolved, or explicitly flagged.

## Headless ceiling

`clearmetric.lineage` is headless: it does not connect to a warehouse and cannot
hydrate live schema at runtime.

That means:

- `SELECT *` resolves only when manifest or folder input already provides enough
  schema metadata to expand stars safely
- star-heavy compiled dbt projects may remain heavily flagged rather than
  silently incomplete
- on the committed Shopify closure, roughly 0.7% of local-model columns are flagged and 0%
  are silent — flagging is honesty, not product failure

The launch claim is **not** universal dbt column lineage. It is: correct where
resolvable, explicit where not, no credentials.

## Observed boundaries from validation

Validation against the committed corpus shows:

| Fixture | Total columns | Resolved | Flagged | Source leaf | Silent | Notes |
|---|---:|---:|---:|---:|---:|---|
| `jaffle_shop` | 38 | 0 | 27 | 11 | 0 | Star-heavy staging; strict R6 suppresses column edges. |
| `loom_finance` | 10 | 0 | 4 | 6 | 0 | Star projection on staging model. |
| `loom_marketing` | 11 | 0 | 4 | 7 | 0 | Star projection on staging model. |
| `shopify` | 436 | 0 | 436 | 0 | 0 | Production-shaped closure; star-heavy compiled SQL flagged under strict R6. |
| `sql_folder` | 7 | 7 | 0 | 0 | 0 | Explicit projections only; full column lineage. |

Observed behavior from that sweep:

- plain `SELECT *` with known schema can resolve exactly
- plain `SELECT *` without schema degrades honestly with warnings
- `table.*` with an alias currently warns instead of inventing fake alias-root lineage
- malformed SQL in one file no longer kills an entire SQL-folder run
- complex compiled dbt SQL can remain warning-rich even when some exact probes inside the same project still pass

The expanded public Shopify closure is the clearest example of the last point:
the package now resolves many exact paths inside the project, and the former
silent miss on `shopify__customers.lifetime_total_spent` is no longer silent,
but the corpus still emits many warnings. That is an honest limitation, not a
silent success case.

## Impact-analysis scope

The launch claim should stay scoped to **column and dataset impact through
`derives_from` / `depends_on` edges**.

`derives_from` is intentionally **value-lineage**, not universal reference
lineage.

That means the package includes columns whose values flow into an output
expression, and excludes predicate-only references such as:

- `CASE WHEN` condition columns
- `JOIN ... ON` condition columns
- `WHERE` / `HAVING` condition columns
- window `PARTITION BY` keys

Value-lineage filtering follows the defining expression through CTE
re-projection: when an output column is projected from a CTE, the filter uses
the shallowest downstream expression that still contains predicate logic (for
example `SUM(CASE WHEN ... THEN amount ...)`) rather than the outer column
reference alone.

### Strict value-lineage rules (R6–R8)

Under strict value-lineage, warnings and edges do not coexist for the same
failure mode:

- **R6 (`select_star`):** When `SELECT *` or `alias.*` is present, the engine
  emits `select_star` and does **not** enumerate star-expanded outputs as
  `derives_from` edges. Explicit non-star projections still resolve.
- **R7 (UNION):** When SQL contains `UNION` / `UNION ALL`, the engine emits
  `unresolved_lineage` and does **not** emit positional branch-merge edges.
- **R8 (quoted / unresolved outputs):** When an output column is flagged
  `unresolved_lineage` because quoting or normalization prevents confident
  resolution, the engine does **not** emit `derives_from` for that column.

The enterprise adversarial manifest validates these rules against an independent
hand-derived oracle (`value_lineage_expected.yml`): 39 adversarial model edges +
14 staging source edges = **53** total under strict semantics (down from 70 when
star/union/quoted paths hybrid-warned and edge-enumerated).

### Residual limits

Even with CTE-aware filtering, headless static analysis still cannot guarantee
complete value-lineage on every SQL shape. Residual gaps include:

- dynamic SQL and unexpanded dbt macros invisible in compiled SQL
- sqlglot parse or lineage gaps on warning-rich compiled models
- reference usage that is logically predicate-like but syntactically embedded in
  unsupported expression forms

When resolution is not possible, the tool emits `unresolved_lineage` or other
explicit warnings instead of returning empty traces.

This is deliberate. The question answered is: "what downstream values change if
this column's values change?" It is **not** "every place this column is used in
logic." Users who need reference-lineage semantics will need a different edge
kind in the future.

`clearmetric.lineage` does **not** currently claim coverage for:

- exposures
- metrics or semantic-layer entities
- tests as downstream impact targets
- runtime warehouse-side dependencies outside compiled SQL
- universal reference lineage for predicate usage
- universal exact lineage on arbitrary warning-rich compiled SQL
- column-level correctness on schema-gated corpora without warehouse types

## Composition rule

For SQL lineage composition, dbt models are currently represented as SQL
datasets with `table:` and `column:` IDs so they merge cleanly with
`clearmetric.lineage` artifacts built from compiled SQL.
