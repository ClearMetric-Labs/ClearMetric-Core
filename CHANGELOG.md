# Changelog

All notable changes to this project will be documented in this file.

## 0.5.0 - 2026-06-27

### Added (Backbone v2)

- **`clearmetric.graph`** — canonical `GraphView`, traversal helpers, selector grammar (Phase 0/5)
- **Contract nodes** — `metric` / `query` kinds, `core/contracts.py`, `compiler/contracts.py` validation (Phase 1)
- **Intent adapter** — `adapters/intent.py`, `spec/intent.schema.json`, batch validation errors (Phase 2)
- **Policy gating** — `evaluate_node`, `gate`, `project_for_emit`, adversarial policy tests (Phase 3)
- **`compile --format consumer-catalog --identity ID`** — policy-gated consumer catalog (additive; `catalog` unchanged)
- **`compile --format frontend-contract --identity ID`** — query contract emitter (Phase 4a)
- **`compile_contracts` pipeline stage** — `compiled_sql` on query nodes
- **Runtime (optional)** — `clearmetric.runtime`, `cm query`, `cm serve`, `[runtime]` extra with DuckDB (Phase 4b)
- **CheckSpec registry** — selector-scoped hygiene checks including `duplicate_formula` (Phase 5)
- **Docs** — `docs/backbone-v2-roadmap.md`, `docs/adoption-gate.md`

### Changed

- **`cm impact`** — unfiltered by default; optional `--identity` for governance preview
- **`--identity` required** for `consumer-catalog`, `openlineage`, `frontend-contract`, `ai-context` only
- **Deleted** `project_graph`; use `project_for_emit` for policy-gated projections
- **Deleted** `lineage/graph.py` and `trace_*_from_project` (GraphView consolidation)

## 0.4.0 - 2026-06-27

### Added

- **`compile --format catalog`** — catalog projection (`table`, `column`, `model` nodes only) via `project_catalog_assets` and `emitters/catalog`
- **`core.interop.attach_warehouse_bindings`** — warehouse physical bindings on lineage nodes; `warehouse_bind_unresolved` / `warehouse_bind_ambiguous` warnings
- **Posture-aware cleaner** — `run_compile_checks` / `enforce_checks` with `resolve_severity`; duplicate-binding and partial-derivation checks
- **Compiler validation split** — `build_graph`, `check_graph`, `enforce_graph`; `cm clean` exits 1 on errors only (warnings never fail exit)
- **Optional `aliases` path** in `clearmetric.yaml`; policy rules validated at config load

### Changed

- v1 warehouse path is **INFORMATION_SCHEMA metadata exports only** — no live Snowflake connector stub
- Removed `compare_warehouse_metadata`, `enforce_structural_checks`, `cm connect snowflake`
- Policy YAML failures raise `ProjectConfigError` at config load with path-qualified messages

### Notes

- v1 promise: *Turns dbt or SQL plus warehouse metadata exports into one graph for lineage, impact, schema drift findings, and catalog output.*
- Approved phrasing: *Warehouse-aware lineage using INFORMATION_SCHEMA metadata exports* / *Connect warehouse metadata, dbt, and SQL into one lineage graph.*

## 0.3.0 - 2026-06-27

### Added

- **Warehouse-connected wedge** — project-first CLI via `clearmetric.yaml` (`cm init`, `connect`, `scan`, `compile`, `impact`, `clean`, `contract`)
- **`clearmetric.compiler`** — discover → adapters → merge → structural checks → security floor
- **`clearmetric.adapters`** — warehouse `information_schema` JSON, dbt manifest, SQL folder ingestion (`SOURCE_ORDER`: warehouse, dbt, sql)
- **`clearmetric.policy`** / **`clearmetric.cleaner`** / **`clearmetric.projection`** — centralized security floor, structural checks, identity projection
- **`clearmetric.emitters`** — json, text, openlineage, impact output dispatch
- Graph extensions — `DerivationState`, `PhysicalBinding`, optional `aspects` on nodes/edges
- JSON schemas — `spec/clearmetric-project.schema.json`, `spec/catalog-artifact.schema.json`
- `core.ids.parse_column_selection` — single column selection entry (`orders.amount`, `column:orders.amount`, `column.fct_orders.net_revenue`)
- Wedge example — `examples/wedge-jaffle/`; warehouse fixture at `tests/fixtures/wedge/jaffle_warehouse_schema.json`
- Wedge E2E tests, derivation honesty gate, repository boundary tests for new packages

### Changed

- CLI is project-first only — removed positional `project_input`, `--dialect`, and direct lineage imports
- Removed path-based lineage API wrappers; use `load_project` + `*_from_project` or `compiler.compile`
- Cross-source merge emits `schema_drift` / `source_disagreement` warnings instead of silent winner-picking
- CI release smoke uses temp project + `cm init` / `compile` / `impact`
- Public lineage imports centralized on `clearmetric.lineage` (including `load_project`, `ProjectInput`)

### Notes

- Power BI remains a shipped module but is not in the warehouse CLI source registry (historical 0.3.0 release note)
- `serve`, live query execution, metrics YAML, and full RBAC rule kinds are deferred

## 0.2.0 - 2026-06-25

### Changed

- Renamed project to ClearMetric Core
- Consolidated prior multi-package PyPI layout into `clearmetric-core`
- CLI is `cm`; module entry is `python -m clearmetric.cli`

> Package names in the section below predate the rename.

### Added

- **`catalogkit-powerbi` V1** — PBIP discovery, M source extraction (`pbi_parsers`), PBIR visual bindings, mergeable artifact emission
- **`catalogkit-core` cross-graph interop** — FQN normalization, alias maps, `match_status` on edges, `visual`/`page`/`measure` node kinds, `load_table_alias_map()`, `warehouse_table_fqn_candidates_from_name()`
- Rule 1 kill-test decision memo, V1 boundary doc, lineage contract validation, orchestration guide

### Changed

- `catalogkit-core` bumped to `0.2.0` for interop spine additions
- Native SQL table extraction lives in `catalogkit-powerbi` (not core interop)
- `merge_with_warehouse()` re-expands FQN candidates when resolving placeholder join edges
- Contract doc defines warehouse join namespace, alias file format, and failure policy

## 0.1.9 - 2026-06-21

### Changed

- `catalogkit-lineage` enforces strict value-lineage (R6–R8): `SELECT *`, `UNION`, and quoted/unresolved outputs warn without emitting unsafe `derives_from` edges
- centralized SQL shape detection and edge counting in shared engine modules
- aligned all CatalogKit packages to lockstep versioning at `0.1.9`

### Added

- enterprise adversarial manifest with independent hand-derived value-lineage oracle
- warning-cause variant fixtures and CI trust-gate coverage for oracle and variant tests

## 0.1.8 - 2026-06-21

### Changed

- aligned all CatalogKit packages to lockstep versioning at `0.1.8`
- updated the meta-package to require `catalogkit-core`, `catalogkit-query`, and `catalogkit-lineage` at the same release

## 0.1.7 - 2026-06-21

### Changed

- refreshed the root README to lead with the code-derived catalog positioning, a verified compile snippet, and accurate tool descriptions
- `build_openlineage_export` now accepts a pre-built `CatalogArtifact`, so OpenLineage export can chain after `build_catalog_artifact` without reloading the project

## 0.1.2 - 2026-06-20

### Changed

- aligned `catalogkit-lineage` package exports, README examples, and local development instructions with the existing CatalogKit module conventions
- grouped OpenLineage export inputs by output column instead of emitting one export row per lineage edge
- made lineage traversal selections and manifest compiled-path handling fail loudly on invalid input
- tightened release metadata so the coordinated `0.1.2` package set resolves to the freshly published module versions
- added publish-time version checks so package tags cannot drift from source versions

### Added

- first PyPI release of `catalogkit-lineage` for project-level SQL lineage from dbt manifests and SQL folders
- focused lineage regression coverage for grouped OpenLineage export, unknown traversal selections, manifest path escapes, and duplicate SQL-folder dataset names

### Supported

- `SELECT ...`
- `INSERT ... SELECT ...`
- `CREATE ... AS SELECT ...`

### Deferred

- output column lineage
- join semantics beyond dependency mapping
- wrapper target outputs
- warehouse-aware `SELECT *` expansion
