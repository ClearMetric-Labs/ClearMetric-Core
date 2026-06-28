# `clearmetric.lineage` Contract Validation

**Source of truth:** [`packages/clearmetric-core/docs/contract.md`](../packages/clearmetric-core/docs/contract.md)

**Enterprise adapter:** reference only — integration lag must not drive OSS design.

## Public API (validated)

Path-based wrappers (`build_catalog_artifact(path)`, etc.) were removed. Use project loading + `_from_project` build entry points, artifact-first trace helpers, or the compiler spine for wedge workflows.

```python
from clearmetric.compiler import compile
from clearmetric.lineage import (
    build_catalog_artifact_from_project,
    build_lineage_map_from_project,
    build_openlineage_export,
    load_project,
    trace_downstream_from_artifact,
    trace_upstream_from_artifact,
)
```

| Function | Input | Output |
|---|---|---|
| `load_project(path, dialect=...)` | dbt manifest path or SQL folder | `ProjectInput` |
| `build_lineage_map_from_project(project, dialect=...)` | `ProjectInput` | `LineageMap` |
| `build_catalog_artifact_from_project(project, dialect=...)` | `ProjectInput` | `CatalogArtifact` |
| `trace_*_from_artifact(artifact, selection)` | `"orders.amount"` or `column:orders.amount` | `TraversalResult` |
| `build_openlineage_export(artifact, job_name=...)` | pre-built `CatalogArtifact` | OpenLineage-shaped dict |
| `compile(project_dir)` | directory with `clearmetric.yaml` | `CompiledGraph` |

Column and contract selections are normalized via `clearmetric.core.ids.parse_impact_selection` (columns, metrics, queries). Traversal uses `clearmetric.graph` internally.

## Merge Assumptions for `clearmetric.powerbi`

See core contract for warehouse namespace, alias map, and `match_status` rules.
Lineage emits warehouse `table:` IDs from dbt model names (e.g. `table:orders`).

Power BI remains a shipped module but is **not** part of the warehouse CLI source registry.

Wedge CLI also supports `compile --format catalog`, `clean`, and `contract` — see [`docs/v1-boundary.md`](v1-boundary.md).

## Enterprise Drift (integration lag, not OSS gaps)

| Area | ClearMetric Core OSS | Enterprise repo |
|---|---|---|
| Package pin | Published on PyPI as `clearmetric-core` | May lag behind OSS |
| Adapter | N/A | `LineageMapAdapter` with lazy import |
| Tests | Full corpus in ClearMetric Core | Adapter tests lock expected API |

Enterprise should follow OSS contracts when wiring adapters — not the reverse.

## Optional Composition

```
table:orders  --feeds-->  table:<project>.semantic_model.orders
                          match_status: resolved | ambiguous | unresolved
```

Run modules separately; merge via `clearmetric.core.merge()` or `compile()` for the wedge path.
See [`docs/orchestration.md`](orchestration.md).
