#!/usr/bin/env python3
"""Regenerate example notebooks from templates (maintainer utility)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NOTEBOOKS = REPO / "examples" / "notebooks"


def _load_notebook_setup_module():
    spec = importlib.util.spec_from_file_location(
        "_notebook_setup", NOTEBOOKS / "_notebook_setup.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load _notebook_setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NOTEBOOK_SETUP = _load_notebook_setup_module().format_notebook_setup_cell


def md(text: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": text.splitlines(keepends=True),
    }


def code(text: str) -> dict:
    return {
        "cell_type": "code",
        "metadata": {},
        "source": text.splitlines(keepends=True),
        "outputs": [],
        "execution_count": None,
    }


def nb(*cells) -> dict:
    return {
        "cells": list(cells),
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def setup(setup_args: str, imports: str, body: str) -> dict:
    cell = NOTEBOOK_SETUP(setup_call=setup_args, imports=imports, body=body)
    return code(cell)


def write(name: str, notebook: dict) -> None:
    path = NOTEBOOKS / name
    path.write_text(json.dumps(notebook, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path.name}")


def main() -> None:
    write(
        "01_public_wedge_lineage.ipynb",
        nb(
            md(
                "# 01 — Public wedge: column lineage\n\n"
                "The **canonical wedge walkthrough**. You will see raw inputs, how ClearMetric "
                "standardizes them, what compile produces, and one impact example.\n\n"
                "**SQL chain:** `raw_orders` → `orders_base` → `customer_totals` → `customers_report`\n\n"
                "Next: [02 compile formats](02_compile_formats.ipynb) · "
                "[03 impact analysis](03_impact_analysis.ipynb)"
            ),
            setup(
                "",
                "from _paths import lineage_demo_project, show_raw_sources",
                'PROJECT_DIR = lineage_demo_project()\nprint(f"project: {PROJECT_DIR}")',
            ),
            md(
                "## Raw inputs\n\n"
                "Files on disk **before** adapters run. Paths match `discover()`."
            ),
            code("show_raw_sources(PROJECT_DIR)"),
            md(
                "## Standardize\n\n"
                "`discover` → `ingest_all` → `build_graph` — same APIs as `cm scan` / `cm compile`.\n\n"
                "The warehouse export is a realistic Shopify raw zone (22 tables). The SQL folder "
                "adds three logical models not in that export. "
                "`warehouse_bind_unresolved` warnings are **expected** for SQL-only tables; "
                "column lineage through `raw_orders.amount` is still complete."
            ),
            code(
                "from clearmetric.adapters import ingest_all\n"
                "from clearmetric.compiler import build_graph, compile, discover\n"
                "from clearmetric.core import load_project_config\n"
                "from clearmetric.emitters import emit_compile\n\n"
                "project = load_project_config(PROJECT_DIR)\n"
                'print("=== discover ===")\n'
                "for src in discover(PROJECT_DIR).sources:\n"
                '    print(f"{src.kind}\\t{src.path}")\n\n'
                'print("\\n=== ingest_all ===")\n'
                "for kind, artifact in ingest_all(project):\n"
                '    print(f"{kind}: nodes={len(artifact.nodes)} edges={len(artifact.edges)} warnings={len(artifact.warnings)}")\n\n'
                "built = build_graph(PROJECT_DIR)\n"
                'print("\\n=== merged graph (text) ===")\n'
                'print(emit_compile("text", built))'
            ),
            md(
                "## Compile (enforce)\n\n"
                "`compile()` runs posture checks and returns the enforced graph used by impact and emitters."
            ),
            code(
                "compiled = compile(PROJECT_DIR)\n"
                "artifact = compiled.artifact\n"
                'print(f"nodes: {len(artifact.nodes)}  edges: {len(artifact.edges)}  warnings: {len(artifact.warnings)}")'
            ),
            md(
                "## Impact preview\n\n"
                "Selection strings use `table.column`. Ground truth for this fixture:\n\n"
                "| Selection | Direction | Related columns |\n"
                "|-----------|-----------|------------------|\n"
                "| `customers_report.customer_lifetime_value` | upstream | 3 |\n"
                "| `orders_base.amount` | downstream | 2 |\n\n"
                "Notebook **03** goes deeper on impact semantics and renderers."
            ),
            code(
                "from clearmetric.compiler.impact import impact\n\n"
                'UPSTREAM = {"column:customer_totals.total_amount", "column:orders_base.amount", "column:raw_orders.amount"}\n'
                'DOWNSTREAM = {"column:customer_totals.total_amount", "column:customers_report.customer_lifetime_value"}\n\n'
                '_, upstream = impact(PROJECT_DIR, selection="customers_report.customer_lifetime_value", direction="upstream")\n'
                '_, downstream = impact(PROJECT_DIR, selection="orders_base.amount", direction="downstream")\n\n'
                "assert set(upstream.related_ids) == UPSTREAM\n"
                "assert set(downstream.related_ids) == DOWNSTREAM\n"
                'print("upstream:", sorted(upstream.related_ids))\n'
                'print("downstream:", sorted(downstream.related_ids))'
            ),
        ),
    )

    write(
        "02_compile_formats.ipynb",
        nb(
            md(
                "# 02 — Compile formats\n\n"
                "Same enforced graph, **different emitted shapes** for different consumers.\n\n"
                "| Format | Purpose |\n"
                "|--------|--------|\n"
                "| `json` | Full graph artifact (includes compile warnings) |\n"
                "| `catalog` | Catalog-oriented artifact (warnings stripped) |\n"
                "| `openlineage` | OpenLineage interop |\n"
                "| `text` | Human-readable summary |\n\n"
                "Assumes you read **01** for raw → standardize context."
            ),
            setup(
                "",
                "import json\nfrom _paths import lineage_demo_project",
                "PROJECT_DIR = lineage_demo_project()",
            ),
            md("## Compile once"),
            code(
                "from clearmetric.compiler import compile\n"
                "from clearmetric.emitters import emit_compile\n"
                "from clearmetric.emitters.registry import WEDGE_COMPILE_FORMATS\n\n"
                "compiled = compile(PROJECT_DIR)\n"
                'graph = json.loads(emit_compile("json", compiled))\n'
                'catalog = json.loads(emit_compile("catalog", compiled))\n'
                'ol = json.loads(emit_compile("openlineage", compiled))\n'
                'text = emit_compile("text", compiled)\n\n'
                'print("public wedge formats:", WEDGE_COMPILE_FORMATS)'
            ),
            md("## `json` — full graph\n\nOne real node and one real lineage edge."),
            code(
                "print(json.dumps({\n"
                '    "top_level_keys": sorted(graph.keys()),\n'
                '    "node_count": len(graph["nodes"]),\n'
                '    "edge_count": len(graph["edges"]),\n'
                '    "warning_count": len(graph["warnings"]),\n'
                "}, indent=2))\n\n"
                'example_node = next(n for n in graph["nodes"] if n["id"] == "column:orders_base.amount")\n'
                'example_edge = next(e for e in graph["edges"] if e["source_id"] == "column:orders_base.amount")\n'
                'print("\\nexample node:")\n'
                "print(json.dumps(example_node, indent=2))\n"
                'print("\\nexample edge:")\n'
                "print(json.dumps(example_edge, indent=2))"
            ),
            md(
                "## `catalog` — catalog contract\n\n"
                "For this fixture, **the same physical nodes and edges** appear in `catalog`. "
                "The difference is the **emitted contract**: compile warnings are removed, "
                "and warehouse-bound columns include `bindings`."
            ),
            code(
                'graph_node_ids = {n["id"] for n in graph["nodes"]}\n'
                'catalog_node_ids = {n["id"] for n in catalog["nodes"]}\n'
                "print(json.dumps({\n"
                '    "catalog_node_count": len(catalog["nodes"]),\n'
                '    "catalog_warning_count": len(catalog["warnings"]),\n'
                '    "same_node_ids_as_graph": catalog_node_ids == graph_node_ids,\n'
                "}, indent=2))\n\n"
                'catalog_slice = [n for n in catalog["nodes"] if n["id"] in {\n'
                '    "table:raw_orders", "column:raw_orders.amount", "column:orders_base.amount",\n'
                "}]\n"
                'print("\\nexample catalog slice:")\n'
                "print(json.dumps(catalog_slice, indent=2))"
            ),
            md(
                "## `openlineage` — interop shape\n\nFull job, datasets, and column lineage for this fixture."
            ),
            code(
                "print(json.dumps({\n"
                '    "top_level_keys": sorted(ol.keys()),\n'
                '    "dataset_count": len(ol.get("datasets") or []),\n'
                '    "column_lineage_count": len(ol.get("columnLineage") or []),\n'
                "}, indent=2))\n"
                'print("\\nopenlineage payload:")\n'
                "print(json.dumps({\n"
                '    "job": ol["job"],\n'
                '    "datasets": ol.get("datasets") or [],\n'
                '    "columnLineage": ol.get("columnLineage") or [],\n'
                "}, indent=2))"
            ),
            md("## `text` — CLI-style summary"),
            code("print(text)"),
            md("## Validate"),
            code(
                "from clearmetric.core.validate import validate_artifact_dict\n\n"
                "validate_artifact_dict(graph)\n"
                "validate_artifact_dict(catalog)\n"
                'print("graph and catalog pass catalog-artifact.schema.json")'
            ),
        ),
    )

    write(
        "03_impact_analysis.ipynb",
        nb(
            md(
                "# 03 — Impact analysis\n\n"
                "Impact answers: *where does this column come from?* (upstream) and "
                "*what breaks if I change it?* (downstream).\n\n"
                "**Two intentional selections** for `lineage-demo`:\n\n"
                "| Selection | Role | Direction | Expected |\n"
                "|-----------|------|-----------|----------|\n"
                "| `customers_report.customer_lifetime_value` | sink / report column | upstream | 3 columns |\n"
                "| `orders_base.amount` | mid-pipeline column | downstream | 2 columns |\n\n"
                "Downstream from the sink column is **empty** — that is correct, not an error."
            ),
            setup(
                "",
                "import json\nfrom _paths import lineage_demo_project",
                "PROJECT_DIR = lineage_demo_project()",
            ),
            md("## Mid-pipeline: downstream from `orders_base.amount`"),
            code(
                "from clearmetric.compiler.impact import impact\n\n"
                "DOWNSTREAM_AMOUNT = {\n"
                '    "column:customer_totals.total_amount",\n'
                '    "column:customers_report.customer_lifetime_value",\n'
                "}\n"
                '_, amount_down = impact(PROJECT_DIR, selection="orders_base.amount", direction="downstream")\n'
                "assert set(amount_down.related_ids) == DOWNSTREAM_AMOUNT\n"
                'print("orders_base.amount downstream:", sorted(amount_down.related_ids))'
            ),
            md(
                "## Sink column: upstream from `customers_report.customer_lifetime_value`"
            ),
            code(
                "UPSTREAM_CLV = {\n"
                '    "column:customer_totals.total_amount",\n'
                '    "column:orders_base.amount",\n'
                '    "column:raw_orders.amount",\n'
                "}\n"
                "compiled, upstream = impact(\n"
                "    PROJECT_DIR,\n"
                '    selection="customers_report.customer_lifetime_value",\n'
                '    direction="upstream",\n'
                ")\n"
                "_, downstream_clv = impact(\n"
                "    PROJECT_DIR,\n"
                '    selection="customers_report.customer_lifetime_value",\n'
                '    direction="downstream",\n'
                ")\n"
                "assert set(upstream.related_ids) == UPSTREAM_CLV\n"
                "assert downstream_clv.related_ids == []\n"
                'print("upstream:", sorted(upstream.related_ids))\n'
                'print("downstream (empty for sink):", downstream_clv.related_ids)'
            ),
            md("## `emit_impact` — JSON output"),
            code(
                "from clearmetric.core.validate import validate_impact_output_dict\n"
                "from clearmetric.emitters.impact import emit_impact\n\n"
                'impact_json = emit_impact(compiled, upstream, format="json", direction="upstream")\n'
                "payload = json.loads(impact_json)\n"
                "validate_impact_output_dict(payload)\n"
                'print("keys:", sorted(payload.keys()))\n'
                'print("derivation entries:", len(payload["derivation"]))\n'
                'print(json.dumps(payload["derivation"], indent=2))'
            ),
            md("## Renderers — tree and mermaid"),
            code(
                'print(emit_impact(compiled, upstream, format="tree", direction="upstream"))\n'
                'print("\\n--- mermaid ---")\n'
                'print(emit_impact(compiled, upstream, format="mermaid", direction="upstream"))'
            ),
        ),
    )

    write(
        "04_consumer_bundle.ipynb",
        nb(
            md(
                "# 04 — Consumer artifact bundle\n\n"
                "Thin apps read **pre-emitted JSON bundles** — no browser-side policy.\n\n"
                "```text\n"
                "cm compile / cm impact → bundle.manifest.json → catalog-viewer / lineage-explorer\n"
                "```\n\n"
                "| Bundle | Viewer | Impact focus |\n"
                "|--------|--------|-------------|\n"
                "| `minimal` | catalog-viewer | upstream from `orders_base.amount` |\n"
                "| `lineage-demo` | lineage-explorer | downstream from `orders_base.amount` |\n\n"
                "Viewers run locally via `python -m http.server` — see "
                "[consumers README](../consumers/README.md). In Colab we inspect bundle JSON in-notebook."
            ),
            setup(
                "",
                "import json\nfrom _paths import (\n"
                "    build_bundle_script,\n"
                "    consumer_bundle_dir,\n"
                "    consumer_scenario,\n"
                "    lineage_demo_project,\n"
                "    load_checks_runner,\n"
                "    repo_root,\n"
                ")\n",
                "BUNDLE_DIR = consumer_bundle_dir()\n"
                'LINEAGE_BUNDLE_DIR = consumer_bundle_dir("lineage-demo")\n'
                "SCENARIO = consumer_scenario()\n"
                "SCENARIO_PROJECT = lineage_demo_project()\n"
                'print(f"minimal bundle: {BUNDLE_DIR}")\n'
                'print(f"scenario project: {SCENARIO_PROJECT}")',
            ),
            md("## Manifest tour (`minimal`)"),
            code(
                "from clearmetric.core.validate import (\n"
                "    load_artifact_file,\n"
                "    load_bundle_manifest_file,\n"
                "    load_impact_output_file,\n"
                ")\n\n"
                'manifest = load_bundle_manifest_file(BUNDLE_DIR / "bundle.manifest.json")\n'
                "print(json.dumps({\n"
                '    "scenario_id": manifest["scenario_id"],\n'
                '    "lane": manifest["artifacts"]["graph"]["lane"],\n'
                '    "artifacts": list(manifest["artifacts"].keys()),\n'
                '    "default_impact": manifest["defaults"]["impact_key"],\n'
                "}, indent=2))\n\n"
                'graph = load_artifact_file(BUNDLE_DIR / manifest["artifacts"]["graph"]["path"])\n'
                'catalog = load_artifact_file(BUNDLE_DIR / manifest["artifacts"]["catalog"]["path"])\n'
                'print(f"graph nodes: {len(graph.nodes)}  catalog nodes: {len(catalog.nodes)}")'
            ),
            md("## Default impact artifact"),
            code(
                'impact_key = manifest["defaults"]["impact_key"]\n'
                'impact_ref = manifest["artifacts"]["impacts"][impact_key]\n'
                'impact = load_impact_output_file(BUNDLE_DIR / impact_ref["path"])\n'
                "print(f\"selection_id: {impact['selection_id']}\")\n"
                "print(f\"related_ids: {impact['related_ids']}\")\n"
                "print(f\"warnings: {[w['code'] for w in impact['warnings']]}\")"
            ),
            md("## `lineage-demo` bundle (downstream trace)"),
            code(
                'lineage_manifest = load_bundle_manifest_file(LINEAGE_BUNDLE_DIR / "bundle.manifest.json")\n'
                'lineage_key = lineage_manifest["defaults"]["impact_key"]\n'
                'lineage_ref = lineage_manifest["artifacts"]["impacts"][lineage_key]\n'
                'lineage_impact = load_impact_output_file(LINEAGE_BUNDLE_DIR / lineage_ref["path"])\n'
                'print(f"impact: {lineage_key}")\n'
                'for node_id in lineage_impact["related_ids"]:\n'
                '    print(f"  {node_id}")'
            ),
            md("## Corpus checks (centralized runner)"),
            code(
                "checks = load_checks_runner()\n"
                'violations = checks.run_checks(BUNDLE_DIR, SCENARIO / "checks.yaml")\n'
                "if violations:\n"
                '    raise AssertionError("\\n".join(violations))\n'
                'print("minimal corpus checks passed")'
            ),
            md(
                "## Optional: regenerate bundle (full clone only)\n\n"
                "Requires a local checkout with `scripts/consumers/build_bundle.py`."
            ),
            code(
                "import subprocess\n\n"
                "try:\n"
                "    script = build_bundle_script()\n"
                "except FileNotFoundError as exc:\n"
                '    print(f"skip regenerate: {exc}")\n'
                "else:\n"
                "    import tempfile\n"
                "    with tempfile.TemporaryDirectory() as tmp:\n"
                '        out = Path(tmp) / "bundle"\n'
                "        result = subprocess.run(\n"
                '            [sys.executable, str(script), "--scenario", str(SCENARIO), "--out", str(out)],\n'
                "            capture_output=True,\n"
                "            text=True,\n"
                "            cwd=str(repo_root()),\n"
                "        )\n"
                "        if result.returncode != 0:\n"
                "            raise RuntimeError(result.stderr or result.stdout)\n"
                "        print(result.stdout.strip())\n"
                '        print("rebuilt bundle validates OK")'
            ),
        ),
    )

    write(
        "05_backbone_lab_experimental.ipynb",
        nb(
            md(
                "# 05 — Backbone lab (experimental)\n\n"
                "> **Experimental / internal — not part of the public product promise.**\n\n"
                "Notebook **04** shows **admin-lane V0 bundles**. This notebook shows **lab consumer formats** "
                "(`consumer-catalog`, `frontend-contract`, `ai-context`) gated by RBAC policy.\n\n"
                "Requires `clearmetric-core[runtime]` for DuckDB query cells."
            ),
            setup(
                'extras="runtime"',
                "import json\nfrom _paths import backbone_lab_project",
                'os.environ["CM_EXPERIMENTAL"] = "1"\n'
                "PROJECT_DIR = backbone_lab_project()\n"
                'IDENTITY = "analyst"\n'
                'print(f"project: {PROJECT_DIR}")',
            ),
            md("## Discover and ingest"),
            code(
                "from clearmetric.adapters import ingest_source\n"
                "from clearmetric.compiler import compile, discover\n"
                "from clearmetric.core import load_project_config\n\n"
                "project = load_project_config(PROJECT_DIR)\n"
                "for src in discover(PROJECT_DIR).sources:\n"
                '    print(f"{src.kind}\\t{src.path}")\n'
                'for kind in ("warehouse", "dbt", "intent"):\n'
                "    artifact = ingest_source(kind, project)\n"
                '    print(f"{kind}: nodes={len(artifact.nodes)} edges={len(artifact.edges)}")\n\n'
                "compiled = compile(PROJECT_DIR)"
            ),
            md("## Admin vs consumer catalog"),
            code(
                "from clearmetric.emitters import emit_compile\n"
                "from clearmetric.emitters.registry import LAB_COMPILE_FORMATS\n\n"
                'admin_catalog = json.loads(emit_compile("catalog", compiled))\n'
                'consumer = json.loads(emit_compile("consumer-catalog", compiled, identity=IDENTITY))\n'
                'admin_ids = {n["id"] for n in admin_catalog["nodes"]}\n'
                'consumer_ids = {n["id"] for n in consumer["payload"]["nodes"]}\n'
                'print("lab formats:", LAB_COMPILE_FORMATS)\n'
                'print(f"admin nodes: {len(admin_ids)}  consumer nodes: {len(consumer_ids)}")\n'
                'print("consumer-only examples:", sorted(consumer_ids - admin_ids))'
            ),
            md(
                "## Policy-gated impact\n\n"
                "`orders.amount --upstream` is empty in this lab because the fixture has no "
                "column-level derivation into `orders.amount`. To demonstrate gated impact, "
                "trace the query's declared dependency instead: the analyst can see the "
                "related column, while an unprivileged viewer is denied at the selection."
            ),
            code(
                "from clearmetric.compiler.impact import impact\n"
                "from clearmetric.core.errors import PolicyDeniedError\n\n"
                'SELECTION = "query:executive_revenue"\n'
                'DIRECTION = "downstream"\n'
                "_, ungated = impact(PROJECT_DIR, selection=SELECTION, direction=DIRECTION)\n"
                "_, analyst = impact(\n"
                "    PROJECT_DIR,\n"
                "    selection=SELECTION,\n"
                "    direction=DIRECTION,\n"
                "    identity=IDENTITY,\n"
                ")\n"
                'assert analyst.related_ids == ["column:orders.amount"]\n'
                'print(f"selection: {analyst.selection_id}")\n'
                'print("ungated related ids:", ungated.related_ids)\n'
                'print("analyst related ids:", analyst.related_ids)\n\n'
                "try:\n"
                '    impact(PROJECT_DIR, selection=SELECTION, direction=DIRECTION, identity="viewer")\n'
                "except PolicyDeniedError as exc:\n"
                '    print("viewer denied:", exc)'
            ),
            md("## Frontend contract and AI context"),
            code(
                'contracts = json.loads(emit_compile("frontend-contract", compiled, identity=IDENTITY))\n'
                'ai_context = json.loads(emit_compile("ai-context", compiled, identity=IDENTITY))\n'
                'print("consumer envelope keys:", sorted(contracts.keys()))\n'
                'print("frontend-contract query:", contracts["payload"]["queries"][0]["id"])\n'
                "print(\n"
                '    "ai-context node kinds:",\n'
                "    sorted({node['id'].split(':')[0] for node in ai_context['payload']['nodes']}),\n"
                ")"
            ),
            md("## Execute query (requires runtime)"),
            code(
                "from clearmetric.runtime import execute_project_query\n\n"
                "rows = execute_project_query(\n"
                "    compiled.artifact,\n"
                "    identity=IDENTITY,\n"
                "    rules_path=compiled.project.policy.rules,\n"
                '    query_selection="query:executive_revenue",\n'
                "    project_dir=PROJECT_DIR,\n"
                ")\n"
                "print(rows)"
            ),
        ),
    )


if __name__ == "__main__":
    main()
