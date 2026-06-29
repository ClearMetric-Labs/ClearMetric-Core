# ClearMetric Core

**One canonical graph of your analytics layer — compiled from SQL, dbt, and warehouse
metadata you already have.**

ClearMetric merges warehouse metadata, dbt, and SQL into a single graph with physical
bindings, derivation state, and explicit warnings when something cannot be resolved. Use it
for lineage, impact analysis, catalog output, structural checks, and OpenLineage export —
all from the same compiled graph.

```bash
pip install clearmetric-core
cd examples/lineage-demo
cm scan
cm compile --format json > graph.json
cm compile --format catalog > catalog.json
cm impact orders_base.amount --downstream
cm clean
cm contract graph.json
```

Warehouse metadata is a **local INFORMATION_SCHEMA JSON export** — not a live connector.

If `cm` is occupied on your PATH: `python -m clearmetric.cli --project-dir . …`

> **Status:** early development (0.x). Pin your versions. **Docs:** [clearmetric-labs.github.io/ClearMetric-Core](https://clearmetric-labs.github.io/ClearMetric-Core/) · Architecture: [`docs/public-architecture.md`](docs/public-architecture.md) · [Validation](https://clearmetric-labs.github.io/ClearMetric-Core/validation/what-works/)

## Features

- **Lineage + impact** — upstream/downstream column traversal; answer *what breaks if I rename this column?*
- **One graph** — warehouse, dbt, and SQL merged with physical bindings on lineage nodes
- **Catalog** — `compile --format catalog` for table/column/model nodes
- **OpenLineage** — `compile --format openlineage` for interop with DataHub, Marquez, etc.
- **Cleaner + security floor** — structural checks and schema drift warnings; compile fails closed on security policy errors (see [`docs/limitations.md`](docs/limitations.md))

## Quickstart

Start with the self-contained [`examples/lineage-demo`](examples/lineage-demo/) project (SQL pipeline + warehouse JSON).

```bash
pip install clearmetric-core
cd examples/lineage-demo
cm scan
cm compile --format json > graph.json
cm impact orders_base.amount --downstream
cm clean
cm contract graph.json
```

Column selections accept `orders.amount`, `column:orders.amount`, or `column.fct_orders.net_revenue` (normalized via `clearmetric.core.ids.parse_column_selection`).

## Compile the graph

Adapters normalize sources in; the core merges and binds; validation runs through the cleaner and security floor; emitters shape output.

```python
from pathlib import Path
from clearmetric.compiler import build_graph, check_graph, compile

compiled = compile(Path("./my-project"))          # build + enforce
built = build_graph(Path("./my-project"))           # build pipeline without enforce
report = check_graph(built.artifact, posture=built.project.posture)  # report-only (same path as cm clean)
```

## Modules

One install (`pip install clearmetric-core`) — Python subpackages, not separate PyPI packages.

| Module | Role |
|--------|------|
| **`clearmetric.graph`** | `GraphView`, impact traversal, traversal render, selectors, graph slices |
| **`clearmetric.lineage`** | SQL/dbt artifact build |
| **`clearmetric.compiler`** | `build_graph`, `check_graph`, `enforce_graph`, CLI orchestration |
| **`clearmetric.adapters`** | INFORMATION_SCHEMA JSON, dbt manifest, SQL folders |
| **`clearmetric.core`** | Artifact, canonical IDs, merge, bindings interop |
| **`clearmetric.cleaner`** | Posture-aware checks |
| **`clearmetric.policy`** | Compile-time security floor and policy primitives |
| **`clearmetric.projection`** | Policy-aware graph projection |
| **`clearmetric.emitters`** | Registry-backed compile formats, catalog/OpenLineage/admin JSON |
| **`clearmetric.query`** | Single-statement SQL structure and contract support |
| **`clearmetric.powerbi`** | PBIP lineage API |

## CLI

| Command | Purpose |
|---------|---------|
| `cm init` | Scaffold `clearmetric.yaml` + `policy/rules.yaml` |
| `cm connect warehouse --information-schema PATH` | Attach local metadata export |
| `cm scan` | List configured sources (warehouse, dbt, sql, optional aliases) |
| `cm compile --format json\|text\|catalog\|openlineage` | Build + enforce graph to stdout |
| `cm impact SELECTION --upstream\|--downstream` | Column lineage (enforced graph) |
| `cm clean` | Report findings; exit 1 on **errors only** |
| `cm contract ARTIFACT.json` | Schema validate + strict enforce (CI) |

## Limits

Static analysis for SQL/dbt lineage; warehouse **metadata exports** only in the public CLI.
ClearMetric does not connect to live warehouses or execute production queries. On star-heavy
SQL (`SELECT *` without schema), ClearMetric flags what it cannot resolve. See
[`docs/limitations.md`](docs/limitations.md) (pointers to Validation and the resolver spec) and the
[Docs site](https://clearmetric-labs.github.io/ClearMetric-Core/).

## Feedback

- **Bugs / features:** [Issues](https://github.com/ClearMetric-Labs/ClearMetric-Core/issues)
- **Questions:** [Discussions](https://github.com/ClearMetric-Labs/ClearMetric-Core/discussions)

## License

Apache 2.0.

---

<details>
<summary><strong>Architecture & contributing</strong></summary>

ClearMetric Core is one package at `packages/clearmetric-core`. See
[`docs/public-architecture.md`](docs/public-architecture.md) for v1 scope and
[`clearmetric-architecture.md`](clearmetric-architecture.md) for the full historical design.

**Docs:** [ClearMetric Core Docs](https://clearmetric-labs.github.io/ClearMetric-Core/) · [public architecture](docs/public-architecture.md) · [contract](docs/reference/contract.md) · [contributing](CONTRIBUTING.md)

**Local development**

```bash
python -m pip install -e "packages/clearmetric-core[dev,runtime,release]"
python -m pytest -v packages/clearmetric-core/tests tests/
```

**Learn by notebook:** [examples/notebooks/](examples/notebooks/README.md) — interactive walkthroughs of the wedge, compile formats, consumer bundles, and backbone lab.

Contributions require the [CLA](CLA.md).

</details>
