# What it does today

ClearMetric Core v1 is a **local compiler** for warehouse-aware column lineage and impact.
It does not connect to live warehouses or execute production queries.

## Public CLI

| Command | Purpose |
|---------|---------|
| `cm init` | Scaffold `clearmetric.yaml` + policy rules |
| `cm connect warehouse --information-schema PATH` | Attach local metadata JSON export |
| `cm scan` | List configured sources |
| `cm compile --format …` | Build and enforce graph to stdout |
| `cm impact SELECTION --upstream\|--downstream` | Column lineage traversal |
| `cm clean` | Report findings (exit 1 on errors only) |
| `cm contract ARTIFACT.json` | Schema validate + enforce (CI) |

## Inputs

Warehouse JSON export, dbt `manifest.json`, and/or SQL folders — see [Run on your own project](getting-started/your-project.md).

## Outputs

All public emitters read the same compiled graph. Format flags and commands: [Input and output formats](reference/io-formats.md) · [CLI reference](reference/cli.md).

## Out of scope (v1 public CLI)

Not shipped as public product today:

- Live warehouse connectors (Snowflake, BigQuery, etc.)
- `CM_EXPERIMENTAL`, `cm serve`, `cm query`
- Gated compile formats (`consumer-catalog`, `frontend-contract`, `ai-context`)
- Runtime auth, RBAC/RLS deployment, or policy compiler to warehouse OPA
- Query execution against production data

Compile-time **security floor** and posture-aware cleaner checks apply on enforce paths.
See [Artifact contract](reference/contract.md).

Power BI module scope and full boundary tables: [v1-boundary.md](v1-boundary.md).
