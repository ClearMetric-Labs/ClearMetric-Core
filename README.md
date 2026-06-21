# CatalogKit

**An open-source, dev-first catalog toolkit.** CatalogKit derives lineage, dependencies, and structure from the SQL you already have — runs locally, lives in your repo, no warehouse or platform required.

```bash
pip install catalogkit-lineage
catalogkit-lineage --dialect postgres --upstream orders.credit_card_amount ./manifest.json
```

```
upstream: orders.credit_card_amount
  - column:orders.credit_card_amount
    - column:stg_payments.amount
      - column:raw_payments.amount
```

Ask any column *"what feeds this?"* or *"what breaks if I change it?"* and get a real answer, traced from your dbt project or SQL files.

> **Status:** early development (0.x), release 0.1.5. Pin your versions.

## Why it's different

Most catalogs are heavy platforms you log into and maintain by hand, drifting out of sync with your code. CatalogKit is small, headless tools that derive your catalog *from* the code — version-controlled, composable, and built to run in CI. When it can't resolve something, it flags it instead of guessing.

## Tools

| Tool | What it does | |
|------|--------------|--|
| **`catalogkit-lineage`** | Column-level lineage and impact across a dbt project or `.sql` folder | ✅ |
| **`catalogkit-query`** | Maps a single SQL statement into its tables and dependencies | ✅ |
| **`catalogkit-core`** | Shared artifact format so tool outputs merge into one graph | ✅ |

```bash
pip install catalogkit          # everything
pip install catalogkit-lineage  # just one tool
```

## Roadmap

Growing toward a catalog that lives in your repo and is enforced in CI — one small tool at a time, all derived from your SQL, no new format to adopt:

- ◻️ **`catalogkit-check`** — CI gate: fail a PR when a change breaks something downstream
- ◻️ **`catalogkit-dedupe`** — find duplicate and near-duplicate models
- ◻️ **Catalog compile** — generate a browsable catalog from your repo
- ◻️ **Derived semantics** — surface what your SQL means, flag inconsistent metric definitions

[Open an issue](https://github.com/Clearmetric-Labs/CatalogKit/issues) if there's a primitive you wish existed.

## Limits

Static analysis only — no warehouse connection. On star-heavy SQL (`SELECT *` without schema), it flags what it can't resolve. [Full limitations →](packages/catalogkit-lineage/docs/limitations.md)

[Apache 2.0](LICENSE).

---

<details>
<summary><strong>Architecture & contributing</strong></summary>

CatalogKit is a Python monorepo of independently installable packages sharing the `catalogkit` namespace. Each tool composes through `catalogkit-core` and never depends on another tool, so independent outputs merge into one graph.

**Packages**
- `catalogkit-core` — shared artifact models, canonical IDs, serialization, merge, validation
- `catalogkit-query` — single-statement SQL structure mapping (preserves the `QueryMap` contract)
- `catalogkit-lineage` — project-level lineage for dbt manifests and SQL folders
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
