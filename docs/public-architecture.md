# Public architecture (v1)

ClearMetric Core compiles warehouse metadata, dbt artifacts, and SQL folders into **one
canonical graph** with physical bindings, derivation state, and explicit warnings when
resolution is incomplete.

## What ships in v1

- Installable Python wheel (`clearmetric-core`) with packaged JSON schemas
- CLI: `init`, `connect`, `scan`, `compile`, `impact`, `clean`, `contract`
- Column-level lineage and impact traversal on `derives_from` edges
- Catalog and OpenLineage emitters from the same compiled graph
- Posture-aware cleaner checks and compile-time security floor
- Local INFORMATION_SCHEMA JSON — no live warehouse connector

## Pipeline

1. **Adapters** ingest configured sources into partial artifacts.
2. **Compiler** merges artifacts, binds warehouse tables to dbt/SQL nodes, enforces checks.
3. **Graph** exposes traversals, selectors, and render helpers.
4. **Emitters** shape stdout output (JSON, catalog, OpenLineage, impact JSON).

## Module map

| Module | Role |
|--------|------|
| `clearmetric.core` | Artifact model, IDs, merge, validation, interop |
| `clearmetric.adapters` | Warehouse, dbt, SQL, intent (gated) ingestion |
| `clearmetric.compiler` | Build, check, enforce, CLI orchestration |
| `clearmetric.lineage` | SQL/dbt lineage build from project inputs |
| `clearmetric.graph` | GraphView, impact traversal |
| `clearmetric.cleaner` | Structural and posture-aware findings |
| `clearmetric.emitters` | Format registry and diagnostics |

Full historical narrative lives in [`clearmetric-architecture.md`](https://github.com/ClearMetric-Labs/ClearMetric-Core/blob/main/clearmetric-architecture.md).
Vision and long-term consumers: [`vision.md`](vision.md).

## Quickstart

Adoption path: [Five-minute demo](getting-started/five-minute-demo.md) on the docs site, or
[`examples/lineage-demo/README.md`](https://github.com/ClearMetric-Labs/ClearMetric-Core/blob/main/examples/lineage-demo/README.md).
