# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Changed

- **lineage-demo** — realistic Shopify-style warehouse export (22 tables) + SQL pipeline; notebooks fetch from GitHub when not in a clone
- **Example notebooks** — intentional 01–05 curriculum, Colab-ready `_notebook_setup.py` bootstrap, centralized `_paths.py` asset manifests, raw → standardize teaching flow
- **Notebook verification** — `scripts/verify_notebooks.py` smoke-tests 01–05 locally and in Colab-sim mode (`--colab-sim --skip-pip`)

### Removed

- **`examples/notebooks/_preview.py`** and **`_bootstrap.py`**
- **`examples/consumers/projects/lineage-sql-folder/`** duplicate route
- Shopify dbt source from lineage-demo (use catalog-demo for dbt + warehouse)

## 0.9.1 - 2026-06-28

### Fixed (V1 hardening)

- **Identity tier precedence** — `resolve_table_match()` checks exact matches before suffix bridging per candidate tier; fully-qualified references resolve when two databases share `schema.table`
- **`SELECT *` warning honesty** — expanded stars emit `select_star_expanded` and no longer mark derivation `partial`; conservative stars remain `select_star` / `unresolved_star_source`
- **dbt relation identity** — two-pass manifest resolver with `unique_id → identity` mapping, dbt aspects on table nodes, and sources indexed from the manifest `sources` key

### Added

- **Identity regression tests** — production-path candidate lists and two-database `attach_warehouse_bindings` coverage
- **dbt identity fixtures** — alias, cross-package, cross-schema, warehouse bind, source dependency, and error cases
- **Ground-truth probe floor** — CI enforces `>= 30` probes

### Docs

- **Limitations** — star expansion requires declared upstream columns, not inferred SQL-folder schemas

## 0.9.0 - 2026-06-28

### Added (ClearMetric Core V1 compiler foundation)

- **Schema packaging** — JSON schemas ship in `clearmetric.spec`; repo-root `spec/` removed
- **Compile diagnostics** — stderr summary with `derives_from` counts and zero-lineage warning via `format_compile_diagnostics()`
- **Self-contained examples** — `examples/lineage-demo` and `examples/catalog-demo` replace `wedge-jaffle`
- **Intent gate** — `enabled_sources.intent` requires `CM_EXPERIMENTAL=1`
- **Identity binding** — warehouse identity binding preserves database-qualified names and refuses ambiguous suffix matches
- **Path security** — manifest-relative loaders reject traversal escapes
- **Impact JSON** — `traversed_edges` populated on downstream/upstream traversal
- **Lineage corpus** — 30+ verified ground-truth probes; `SELECT *` expansion when upstream columns are known
- **Docs** — split `public-architecture`, `vision`, `limitations`, and `uncertainty`

### Changed

- **CI** — `examples-smoke`, `/tmp` `release-smoke`, import-linter contract, repo-root `spec/` guard
- **`check.zero_column_lineage`** — cleaner surfaces zero `derives_from` graphs
- **Consumer bundles** — rebuilt for lineage-demo and minimal fixtures

## 0.8.1 - 2026-06-28

### Added (Consumer MVP demo hardening)

- **lineage-demo bundle** — committed sql_folder fixture with non-empty column impact trace for lineage explorer
- **lineage-sql-folder project** — purpose-built ClearMetric project under `examples/consumers/projects/`

### Changed

- **`build_bundle.py`** — atomic staging publish, loud v0 admin-lane guards, required `defaults.impact_key`, single-pass impact recipe parsing
- **Viewers** — subject-only node warnings, graph-level warnings once in catalog viewer, honest empty lineage state
- **artifact-kit** — `graphLevelWarnings()` helper; negative load tests for bad JSON and incomplete manifest

### Docs

- Two-bundle quick start (`minimal` catalog, `lineage-demo` lineage), notebook 04, architecture status line

## 0.8.0 - 2026-06-28

### Added (Consumer MVP Bundle)

- **Bundle contract** — `spec/consumer-bundle.schema.json`, `impact-output.schema.json`, `consumer-envelope.schema.json`; validation in `clearmetric.core.validate`
- **Bundle builder** — `scripts/consumers/build_bundle.py` with `project` and `prebuilt` scenario modes
- **Consumer examples** — committed minimal admin-lane bundle, vanilla `catalog-viewer` and `lineage-explorer`, shared `artifact-kit.mjs`
- **Corpus checks** — declarative `checks.yaml` runner under `tests/consumers/`
- **CLI helper** — `clearmetric.cli.runner.run_cm` (shared by tests and bundle builder)
- **Learning notebooks** — `examples/notebooks/` walkthroughs for wedge, formats, impact, bundles, and backbone lab

### Changed

- **CI** — consumer artifact-kit node tests, consumer pytest slice, repository boundary checks for viewer/build_bundle imports
- **Wedge test helpers** — delegate subprocess invocations to `cli.runner`

### Docs

- [examples/consumers/README.md](examples/consumers/README.md), [examples/notebooks/README.md](examples/notebooks/README.md), updated [docs/e2e-readiness.md](docs/e2e-readiness.md)

## 0.7.1 - 2026-06-28

### Fixed (Backbone boundary QA — experimental lab, wedge outputs stable)

- **Impact `--identity`** — `require_gated_identity` at API boundary; `require_allow` on selection node (loud `PolicyDeniedError`); related ids filtered via `filter_allow_only_ids` (allow only)
- **`apply_policy`** — strips governance aspects on all surviving nodes; filters warnings via `filter_warnings_for_ids`
- **`compile_query_contracts`** — batches malformed query aspect errors with SQL compile errors
- **`validate_contract_nodes`** — loud batch for invalid contract aspects (no silent skip)
- **`execute_project_query`** — `require_gated_identity` at runtime API boundary

### Changed

- **`core.filter_warnings_for_ids`** — single warning visibility helper for graph select and projection
- **`policy.filter_allow_only_ids`** — centralized allow-only visibility for governance preview
- **`ai_context` emitter** — removed duplicate `strip_sensitive_aspects` (projection owns consumer-safe output)

### Docs

- [docs/e2e-readiness.md](docs/e2e-readiness.md), updated [docs/backbone-lab.md](docs/backbone-lab.md) invariants, README `build_graph` comment

## 0.7.0 - 2026-06-28

### Changed (Backbone Scaffold — experimental lab, wedge outputs stable)

- **Graph select** — `clearmetric.graph.select`, `select_kinds`; warnings filtered to visible subjects
- **Projection** — narrowed to `apply_policy` only; kind slicing removed from projection
- **Format registry** — `FormatSpec` + `COMPILE_FORMATS`; sole `emit_compile` dispatch path; sole `gated_context` caller for consumer compile
- **Consumer envelope** — `consumer-catalog`, `frontend-contract`, `ai-context` wrap JSON in `{format, identity, payload, ...}`; admin `json`/`catalog`/`openlineage` stay raw
- **Thin serializers** — emitters serialize artifact only; no policy/projection imports outside registry
- **Runtime** — `execute_project_query` uses `load_rules` + `require_allow`; `runtime.serve` localhost-only debug harness
- **Lab CLI** — `cm serve`, `cm impact --identity`, `ai-context` format (all `CM_EXPERIMENTAL=1`)

### Docs

- Updated [backbone-lab.md](docs/backbone-lab.md), [clearmetric-architecture.md](clearmetric-architecture.md), examples/backbone-lab

## 0.6.1 - 2026-06-28

### Changed (Backbone Lab QA — experimental, not public README promise)

- **Policy context** — `GatedContext`, `gated_context`, and `require_gated_identity` in `policy/load.py` (replaces `load_gated_context` / `emitters/context.py`)
- **Emitter dispatch** — registry is the sole `gated_context` caller; wedge formats ignore identity
- **Compile contracts** — atomic two-pass `compile_query_contracts` (collect all errors, no input mutation)
- **Runtime** — `execute_project_query` gates via `require_allow`, requires fixture seed, executes compiled SQL only
- **CLI boundaries** — `require_gated_compile` / `require_query_identity` normalize identity at the CLI edge
- **Tests** — compile_contracts, policy gate, runtime, lab CLI subprocess, committed example e2e (230 tests)
- **CI** — repository boundary suite includes `test_backbone_lab_boundaries.py`

### Docs

- Updated [backbone-lab.md](docs/backbone-lab.md), [development.md](docs/development.md), and package README for lab QA finish line

## 0.6.0 - 2026-06-28

### Added (Backbone Lab — experimental, not public README promise)

- **Pipeline stages** — `link`, `compile_contracts` after bind; intent ingest via adapter registry
- **Policy gate** — `policy/gate.py`, `require_allow`, `GatedContext` / `gated_context`; projection uses `gate` only
- **Lab emitters** — `consumer-catalog`, `frontend-contract` behind `CM_EXPERIMENTAL=1` and `--identity`
- **Runtime harness** — `clearmetric.runtime`, `cm query` (DuckDB fixtures); gate before execute; no `cm serve`
- **Contracts** — `require_compiled_query_sql` (no raw SQL fallback), `resolve_query_node`, `parse_query_selection`
- **Examples & docs** — `examples/backbone-lab/`, `docs/backbone-lab.md`; adoption gate scoped to public claims only
- **Tests** — MVP demo subprocess flow, backbone lab boundary tests, contract/discover/link coverage

### Changed

- **`openlineage` / `catalog`** — remain ungated admin wedge exports
- **Normal CLI** — wedge commands and formats unchanged without `CM_EXPERIMENTAL=1`

## 0.5.2 - 2026-06-28

### Fixed

- **CI** — ruff import order (`I001`), ruff format, and pyright in policy tests (no functional change from 0.5.1)

## 0.5.1 - 2026-06-28

### Changed (Wedge v1 + Phase 0 consolidation)

- **Impact traversal** moved to `clearmetric.graph.impact`; `clearmetric.lineage` builds artifacts only
- **OpenLineage serialization** moved to `clearmetric.emitters.openlineage` (ungated wedge export)
- **`TraversalResult`** lives in `clearmetric.core.models` (not re-exported from `clearmetric.lineage`)
- **Wedge pipeline** — `discover → ingest → merge → bind` only (no intent/link/compile_contracts in default build)
- **Wedge adapters** — warehouse, dbt, sql only in public registry
- **CLI surface trimmed** — removed `cm query`, `cm serve`, `--identity`, and gated compile formats (`consumer-catalog`, `frontend-contract`, `ai-context`)
- **Boundary tests** — CLI must not import runtime; lineage build/render must not define traversal or OpenLineage export; emitters must not import lineage
- **Traversal render** — `render_traversal_tree` / `render_traversal_mermaid` in `clearmetric.graph.render` (not `clearmetric.lineage`)
- **`discover()`** — reuses `enabled_sources()` from adapter registry (single source of truth for configured sources)
- **Removed dead gated tree** — `clearmetric.runtime`, `compiler/compile_contracts.py`, `compiler/link_metrics.py`, `policy/gate.py`, unused emitter modules

### Docs

- Active scope: wedge + Phase 0; gated roadmap in [`docs/future-roadmap-gated.md`](docs/future-roadmap-gated.md)

## 0.5.0 - 2026-06-27

### Added (Backbone v2 — partially reverted from public CLI in 0.5.1)

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
