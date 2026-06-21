# catalogkit-lineage limitations

`catalogkit-lineage` is intentionally deterministic and headless.

## Supported Input

`catalogkit-lineage` accepts exactly one project input per invocation:

- a dbt `manifest.json` file
- a folder containing one or more `.sql` files

The tool does not run dbt. Manifest input must already point at compiled SQL.

## Guarantees

`catalogkit-lineage` guarantees:

- one supported project input per invocation
- dialect-aware lineage tracing through `sqlglot`
- deterministic table and column IDs through `catalogkit-core`
- project-level upstream and downstream traversal
- a mergeable `CatalogArtifact`
- loud failure on unsupported invocation shapes

## Current Boundaries

`catalogkit-lineage` does **not** currently provide:

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

The deepest real-project coverage today is on `postgres` and `duckdb`. The
`snowflake` and `bigquery` checks are adversarial dialect fixtures, not large
real-project validations.

Folder input is intentionally lighter-weight than dbt manifest input. When a SQL
folder does not provide root-table schema metadata, `SELECT *` leaves at the
external boundary may remain unresolved and will emit warnings instead of
invented column lineage.

## Warning-Based Behavior

`catalogkit-lineage` warns instead of failing when the project input is valid but
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

`catalogkit-lineage` is headless: it does not connect to a warehouse and cannot
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
| `jaffle_shop` | 38 | 27 | 0 | 11 | 0 | Clean baseline dbt fixture. |
| `loom_finance` | 10 | 4 | 0 | 6 | 0 | Small duckdb slice with one star warning. |
| `loom_marketing` | 11 | 4 | 0 | 7 | 0 | Small duckdb slice with one star warning. |
| `shopify` | 1006 | 714 | 7 | 285 | 0 | Production-shaped closure with macro source metadata; 0.7% flagged on local models. |
| `sql_folder` | 7 | 7 | 0 | 0 | 0 | Plain SQL path. |

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

`catalogkit-lineage` does **not** currently claim coverage for:

- exposures
- metrics or semantic-layer entities
- tests as downstream impact targets
- runtime warehouse-side dependencies outside compiled SQL
- universal reference lineage for predicate usage
- universal exact lineage on arbitrary warning-rich compiled SQL

## Composition rule

For SQL lineage composition, dbt models are currently represented as SQL
datasets with `table:` and `column:` IDs so they merge cleanly with
`catalogkit-query` artifacts built from compiled SQL.
