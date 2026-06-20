# CatalogKit

CatalogKit is a lightweight Python monorepo for headless catalog and lineage
tools. Shared graph rules live in `catalogkit-core`, and packages compose
through that core without duplicating shared logic or shared validation
behavior.

## Packages

- `packages/catalogkit-core`: shared artifact models, canonical ID normalization,
  JSON serialization, merge semantics, and validation rules
- `packages/catalogkit-query`: SQL structure mapping for one statement at a time, with
  its existing public `QueryMap` contract preserved
- `packages/catalogkit`: thin meta-package for convenience installs only

Tool packages depend on `catalogkit-core`. Tool packages do not depend on each
other.

## Install

Install only the query tool and shared core:

```bash
python -m pip install catalogkit-query
```

Install the current CatalogKit module set:

```bash
python -m pip install catalogkit
```

Import from the shared namespace:

```python
from catalogkit.core import Node, Edge, Evidence
from catalogkit.query import build_query_map
```

## Repository Layout

```text
CatalogKit/
  packages/
    catalogkit-core/
    catalogkit-query/
    catalogkit/
  docs/
  .github/workflows/
```

## Namespace Rules

- `catalogkit` is a native PEP 420 namespace package.
- No package in this repo may ship `catalogkit/__init__.py`.
- The `catalogkit` meta-package provides dependency metadata only. It must not
  provide an importable `catalogkit` Python package on disk.

## Core Rules

- `version` means shared artifact schema version only.
- Artifact schema versioning is owned by `catalogkit-core`, not package versions.
- Canonical IDs are normalized once in `catalogkit-core` and reused everywhere.
- Artifact merge semantics are defined once in `catalogkit-core`.
- Duplicate shared models or fallback code paths are not allowed.

## Local Development

Install both current packages in editable mode:

```bash
python -m pip install -e packages/catalogkit-core
python -m pip install -e "packages/catalogkit-query[dev,release]"
```

Build the meta-package when you want to validate the convenience install path:

```bash
python -m build packages/catalogkit
```

Run tests:

```bash
python -m pytest -v
```

Build a package locally:

```bash
python -m build packages/catalogkit-core
python -m build packages/catalogkit-query
python -m build packages/catalogkit
```

## Contract Docs

- [`packages/catalogkit-core/docs/contract.md`](packages/catalogkit-core/docs/contract.md)
- [`packages/catalogkit-query/docs/limitations.md`](packages/catalogkit-query/docs/limitations.md)

## Release Notes

- `catalogkit-query` is the active query tool distribution.
- `querymap` should be maintained only as a deprecated pointer package during
  the migration window and must not receive new functionality.

## License

CatalogKit is licensed under Apache 2.0. See [`LICENSE`](LICENSE).
