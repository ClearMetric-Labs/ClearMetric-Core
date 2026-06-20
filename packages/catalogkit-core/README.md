# catalogkit-core

`catalogkit-core` is the shared consistency layer for every CatalogKit module.

It owns:

- artifact schema versioning
- canonical ID normalization
- shared graph models
- deterministic JSON serialization
- merge semantics

It does **not** perform extraction by itself. Tool packages such as `catalogkit-query`
depend on `catalogkit-core` and emit artifacts that follow its contract.

## Install

```bash
python -m pip install catalogkit-core
```

## Imports

```python
from catalogkit.core import CatalogArtifact, Edge, Evidence, Node, Warning, merge
```

For local development:

```bash
python -m pip install -e ".[dev,release]"
```

## Contract

The source of truth for the shared artifact contract is
[`docs/contract.md`](docs/contract.md).
