# CatalogKit

**Build a data catalog from your code, not a platform.** CatalogKit is an open-source toolkit of headless tools that derive lineage, structure, and a mergeable catalog graph from the dbt projects and SQL you already have — locally, no warehouse, no platform to stand up.

```bash
pip install catalogkit-lineage
catalogkit-lineage --dialect postgres --upstream orders.credit_card_amount ./manifest.json
```

```
catalogkit-lineage
upstream: orders.credit_card_amount
selection_id: column:orders.credit_card_amount
tree:
  - column:orders.credit_card_amount
    - column:stg_payments.amount
      - column:raw_payments.amount
```

Ask any column *"what feeds this?"* or *"what breaks if I change it?"* and get a real answer, traced from your code.

> **Status:** early development (0.x), release 0.1.6. Pin your versions.

## Why it's different

Most catalogs are heavy platforms you log into and maintain by hand — and they drift out of sync with the code. CatalogKit is the opposite: small, composable tools that derive the catalog *from* the code, so it stays fresh, lives in your repo, and runs in CI. When it can't resolve something, it flags it instead of guessing.

## Compile a catalog

Every tool emits the same mergeable artifact, so independent outputs combine into one catalog graph — assets, columns, lineage — that you can serialize or hand to other tools via OpenLineage.

```python
from catalogkit.lineage import build_catalog_artifact, build_openlineage_export

catalog = build_catalog_artifact("./manifest.json", dialect="postgres")
open_lineage = build_openlineage_export(catalog)  # standard format others can ingest
```

## Tools

| Tool | What it does | Status |
|------|--------------|--------|
| **`catalogkit-lineage`** | Column-level lineage, impact, catalog graph, OpenLineage export | ✅ Shipped |
| **`catalogkit-query`** | Maps a single SQL statement into its tables and dependencies | ✅ Shipped |
| **`catalogkit-core`** | Shared artifact, canonical IDs, merge | ✅ Shipped |

```bash
pip install catalogkit          # everything
pip install catalogkit-lineage  # just one tool
```

## Roadmap

Growing toward a catalog that lives in your repo and is enforced in CI — one small tool at a time, all derived from your code, no new format to adopt:

- ◻️ **`catalogkit-check`** — CI gate: fail a PR when a change breaks something downstream
- ◻️ **`catalogkit-dedupe`** — find duplicate and near-duplicate models
- ◻️ **Catalog compile** — generate a browsable catalog site from your repo
- ◻️ **Derived semantics** — surface what your SQL means, flag inconsistent metric definitions

[Open an issue](https://github.com/Clearmetric-Labs/CatalogKit/issues) if there's a primitive you wish existed.

## Limits

Static analysis only — no warehouse connection. On star-heavy SQL (`SELECT *` without schema), it flags what it can't resolve rather than guessing. Correct where it can be, explicit where it can't. [Full limitations →](packages/catalogkit-lineage/docs/limitations.md)

## Feedback & contact

Built in the open, and feedback shapes the roadmap.

- **Bugs / features:** [open an issue](https://github.com/Clearmetric-Labs/CatalogKit/issues)
- **Questions / ideas:** [Discussions](https://github.com/Clearmetric-Labs/CatalogKit/discussions)
- **Using it or want to talk?** Reach out on [LinkedIn](https://www.linkedin.com/in/kim-jon).

## License

Apache 2.0.

---

<details>
<summary><strong>Architecture & contributing</strong></summary>

CatalogKit is a Python monorepo of independently installable packages sharing the `catalogkit` namespace. Each tool composes through `catalogkit-core` and never depends on another tool, so independent outputs merge into one graph.

**Packages**
- `catalogkit-core` — shared artifact models, canonical IDs, serialization, merge, validation
- `catalogkit-query` — single-statement SQL structure mapping (preserves the `QueryMap` contract)
- `catalogkit-lineage` — project-level lineage, catalog artifact, and OpenLineage export for dbt manifests and SQL folders
- `catalogkit` — thin meta-package for convenience installs

```python
from catalogkit.core import Node, Edge, Evidence
from catalogkit.query import build_query_map
from catalogkit.lineage import build_lineage_map
```

**Namespace rules**
- `catalogkit` is a native PEP 420 namespace package.
- No package may ship `catalogkit/__init__.py`.
- The meta-package provides dependency metadata only.

**Core rules**
- `version` = shared artifact *schema* version, owned by `catalogkit-core`.
- Canonical IDs and merge semantics defined once in `catalogkit-core`.
- No duplicate shared models or fallback code paths.

**Local development**
```bash
python -m pip install -e packages/catalogkit-core
python -m pip install -e "packages/catalogkit-query[dev,release]"
python -m pip install -e "packages/catalogkit-lineage[dev,release]"
python -m pytest -v
```

**Docs:** [contract](packages/catalogkit-core/docs/contract.md) · [query limits](packages/catalogkit-query/docs/limitations.md) · [lineage limits](packages/catalogkit-lineage/docs/limitations.md)

Contributions require agreeing to the [CLA](CLA.md). See [CONTRIBUTING.md](CONTRIBUTING.md).

</details>
