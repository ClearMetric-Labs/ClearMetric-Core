# Input and output formats

Public wedge commands and formats (from `cm --help`). Generated CLI detail: [CLI reference](cli.md).

## Inputs

| Input | Config | Notes |
|-------|--------|-------|
| Warehouse metadata | `cm connect warehouse --information-schema PATH` | Local JSON export only |
| dbt | `sources.dbt.manifest` in `clearmetric.yaml` | Compiled `manifest.json` |
| SQL | `sources.sql.paths` | Directory of `.sql` files |

## Scan (`cm scan`)

| `--format` | Output |
|------------|--------|
| `json` | Source list as JSON |
| `text` | Human-readable list (default) |

## Compile (`cm compile`)

| `--format` | Output |
|------------|--------|
| `json` | Full graph artifact (default) |
| `catalog` | Catalog slice |
| `openlineage` | OpenLineage event JSON |
| `text` | Human-readable summary |

## Impact (`cm impact SELECTION`)

| `--format` | Output |
|------------|--------|
| `json` | `related_ids`, edges, warnings |
| `text` | Human-readable list |
| `mermaid` | Diagram source |

Requires `--upstream` or `--downstream`.

## Clean and contract

| Command | `--format` | Notes |
|---------|------------|-------|
| `cm clean` | `json`, `text` | Findings report; exit 1 on errors |
| `cm contract FILE` | — | Validates artifact against contract |

Artifact node/edge schema: [Artifact contract](contract.md).
