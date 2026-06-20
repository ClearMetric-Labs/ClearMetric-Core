# catalogkit-query

`catalogkit-query` maps one supported SQL statement into a deterministic `QueryMap`
artifact so you can answer "what feeds what in this query?" fast.

It is a narrow static-analysis tool:

- input: exactly one SQL statement from one SQL file
- output: canonical relations, relation usages, dependency edges, and warnings
- no warehouse credentials
- no dbt project
- no AI key

## Install

```bash
python -m pip install catalogkit-query
```

## Imports

```python
from catalogkit.query import build_catalog_artifact, build_query_map
```

For local development:

```bash
python -m pip install -e ../catalogkit-core
python -m pip install -e ".[dev,release]"
```

## Quickstart

```bash
catalogkit-query --dialect postgres ./examples/ugly_real_world.sql
catalogkit-query --dialect postgres --format json ./examples/ugly_real_world.sql
```

## Output Contract

`catalogkit-query` preserves its public `QueryMap` shape:

- `summary`
- `relations`
- `relation_usages`
- `edges`
- `outputs`
- `warnings`

For CatalogKit composition, the package also exposes a shared
`CatalogArtifact` builder backed by `catalogkit-core`.

The shared core artifact contains:

- `version`
- `nodes`
- `edges`
- `warnings`

## Supported Statements

`catalogkit-query` accepts exactly one supported statement per invocation:

- `SELECT ...`
- `INSERT ... SELECT ...`
- `CREATE ... AS SELECT ...`

Unsupported statement shapes fail loudly.

## Contract Docs

- [`../catalogkit-core/docs/contract.md`](../catalogkit-core/docs/contract.md)
- [`docs/limitations.md`](docs/limitations.md)
