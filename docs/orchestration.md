# Artifact-Level Orchestration (Optional)

Each ClearMetric Core module is **standalone and headless**. The v1 warehouse wedge uses
`clearmetric.compiler` from `clearmetric.yaml`: adapters ingest sources, core merges and
binds warehouse metadata, validation runs through the cleaner and security floor.

Composition with Power BI or query artifacts remains **optional** via `clearmetric.core.merge()`.

Full contract: [`reference/contract.md`](reference/contract.md)

Backbone v2 modules (`clearmetric.graph`, policy-gated projections, intent adapter, runtime) are
built for internal lab use behind `CM_EXPERIMENTAL=1` — see [backbone-lab.md](backbone-lab.md).
Public wedge orchestration remains discover → ingest → merge → bind only. Phases 1–6 public
expansion is documented in [`backbone-v2-roadmap.md`](backbone-v2-roadmap.md) and gated on
[`adoption-gate.md`](adoption-gate.md).

## Wedge usage (recommended)

```bash
pip install clearmetric-core
cm init
cm connect warehouse --information-schema ./warehouse_schema.json
cm scan
cm compile --format json > graph.json
cm compile --format catalog > catalog.json
cm impact orders.amount --upstream --artifact graph.json
cm clean
cm contract graph.json
```

Recompile only when the manifest, engine, or warehouse overlay changes. For repeated impact
queries against a stable graph, persist the compile output and pass `--artifact`:

```bash
cm compile --format json > graph.json
cm contract graph.json
cm impact my_schema.my_model.amount --upstream --artifact graph.json
```

Private corpus harness (`scripts/corpus_external.py`, gitignored `corpus/`) uses generic
engine scoped builds (`build_scoped_lineage_cached`, `loaders.union_build_scope_for_targets`)
via `scripts/_corpus_measurement.py` for reference-baseline compare:

```bash
export CLEARMETRIC_LINEAGE_CACHE_DIR=corpus/reports/_lineage_build_cache
cd packages/clearmetric-core
uv run python scripts/corpus_external.py residual-overlap --repo tuva --force
uv run python scripts/corpus_external.py residual-overlap --repo tuva --evaluate-checkpoints
```

Run long harness builds in `tmux` outside IDE shells; stale cache or checkpoints raise loud
errors — rerun with `--force` after engine or baseline changes.

```python
from pathlib import Path
from clearmetric.compiler import build_graph, check_graph, compile, enforce_graph

# Enforced graph (compile / impact)
compiled = compile(Path("."))
artifact = compiled.artifact

# Report-only checks (same build path as clean)
built = build_graph(Path("."))
report = check_graph(built.artifact, posture=built.project.posture)
```

`compile()` calls `build_graph()` then `enforce_graph()`. `cm clean` calls `build_graph()`
then `check_graph()` so warnings are reported without enforce failing first.

## Standalone module usage

```python
from clearmetric.lineage import build_catalog_artifact_from_project, load_project

project = load_project("./target/manifest.json", dialect="postgres")
artifact = build_catalog_artifact_from_project(project, dialect="postgres")
```

Power BI PBIP lineage via Python API (not in warehouse CLI source registry):

```python
from clearmetric.powerbi import build_catalog_artifact

artifact = build_catalog_artifact("./MyReport.pbip")
```

## Optional composition

```python
from clearmetric.core import load_table_alias_map, merge
from clearmetric.lineage import build_catalog_artifact_from_project, load_project
from clearmetric.powerbi import build_catalog_artifact as build_powerbi, merge_with_warehouse

project = load_project("./target/manifest.json", dialect="postgres")
warehouse = build_catalog_artifact_from_project(project, dialect="postgres")
powerbi = build_powerbi("./MyReport.pbip")

alias_map = load_table_alias_map("./aliases.yaml")  # optional
merged = merge_with_warehouse(powerbi, warehouse, alias_map=alias_map)
```

Project-level aliases in `clearmetric.yaml` use the same alias file format and are applied
during `build_graph()` via `attach_warehouse_bindings()`.

## Rules

1. Module build paths use **`clearmetric.core` only** — sibling imports happen at orchestration/merge time, not inside module parsers.
2. One merge contract — `clearmetric.core.merge()` and core interop helpers only.
3. Cross-source disagreements on non-structural facts become `source_disagreement` / `schema_drift` warnings; structural impossibilities raise `MergeConflictError`.
4. Warehouse physical bindings are attached in core (`attach_warehouse_bindings`); adapters emit metadata nodes only.

## CI

`cm contract graph.json` loads the artifact, validates against packaged `catalog-artifact.schema.json`, and runs `enforce_graph(..., posture="strict")` (structural checks + security floor).
