from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
LINEAGE_ROOT = WORKSPACE_ROOT / "packages" / "catalogkit-lineage"
FIXTURES_ROOT = LINEAGE_ROOT / "tests" / "fixtures"
PROJECTS_ROOT = FIXTURES_ROOT / "projects"
FIXTURES_MD = FIXTURES_ROOT / "FIXTURES.md"

SHOPIFY_REPO = "fivetran/dbt_shopify"
SHOPIFY_REF = "a970f76a01fff8524540714f9255af8e62c3b985"
LOOM_REPO = "p-munhoz/dbt-loom-multi-project-demo"
LOOM_REF = "fddd6600448f13e8b945958630a1ec0abe959bc3"

SHOPIFY_ANCHOR_MODEL_NAMES = {
    "stg_shopify__order",
    "shopify__customers",
    "shopify__transactions",
}

QUOTED_RELATION_PATTERN = re.compile(r'"[^"]+"\."[^"]+"\."([^"]+)"')
MACRO_SOURCE_TABLE_PATTERN = re.compile(r"from\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
FIELDS_CTE_ALIAS_PATTERN = re.compile(
    r"as\s+\n\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:,|\n\s*\)|\n\s*,)",
    re.MULTILINE,
)
JINJA_REF_PATTERN = re.compile(r"\{\{\s*ref\('([^']+)'\)\s*\}\}")
JINJA_CROSS_PROJECT_REF_PATTERN = re.compile(
    r"\{\{\s*ref\('([^']+)'\s*,\s*'([^']+)'\)\s*\}\}"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh vendored catalogkit-lineage fixtures from public dbt sources."
    )
    parser.add_argument(
        "--project",
        choices=("shopify", "loom_finance", "loom_marketing", "all"),
        default="all",
        help="Refresh one fixture or all vendored public fixtures.",
    )
    args = parser.parse_args()

    requested = (
        ("shopify", "loom_finance", "loom_marketing")
        if args.project == "all"
        else (args.project,)
    )
    builders = {
        "shopify": build_shopify_fixture,
        "loom_finance": build_loom_finance_fixture,
        "loom_marketing": build_loom_marketing_fixture,
    }
    for project_name in requested:
        builders[project_name]()
    write_fixtures_md()
    return 0


def build_shopify_fixture() -> None:
    manifest = fetch_json(
        f"https://raw.githubusercontent.com/{SHOPIFY_REPO}/{SHOPIFY_REF}/docs/manifest.json"
    )
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        raise SystemExit("Shopify docs manifest is missing a nodes object.")

    selected_model_ids = transitive_model_closure(
        nodes,
        anchor_names=SHOPIFY_ANCHOR_MODEL_NAMES,
    )
    selected_nodes: dict[str, dict[str, Any]] = {}
    for unique_id, payload in nodes.items():
        if unique_id not in selected_model_ids:
            continue
        selected_nodes[unique_id] = {
            "resource_type": "model",
            "name": payload["name"],
            "unique_id": unique_id,
            "compiled_code": rewrite_shopify_compiled_sql(
                str(payload.get("compiled_code") or "")
            ),
            "depends_on": payload.get("depends_on", {"nodes": []}),
            "columns": payload.get("columns", {}),
        }

    enrich_shopify_closure(selected_nodes)

    selected_names = {node["name"] for node in selected_nodes.values()}
    if not SHOPIFY_ANCHOR_MODEL_NAMES.issubset(selected_names):
        missing = sorted(SHOPIFY_ANCHOR_MODEL_NAMES - selected_names)
        raise SystemExit(f"Shopify fixture selection is incomplete: missing {missing}")

    payload = {
        "metadata": {
            "project_name": "shopify_slice",
            "source": SHOPIFY_REPO,
            "ref": SHOPIFY_REF,
        },
        "nodes": selected_nodes,
        "child_map": child_map_from_nodes(selected_nodes),
    }
    write_fixture_json("shopify", payload)


def build_loom_finance_fixture() -> None:
    compiled_sql = compile_loom_sql(
        fetch_text(
            f"https://raw.githubusercontent.com/{LOOM_REPO}/{LOOM_REF}/projects/finance_project/models/fct_revenue_by_customer.sql"
        )
    )
    payload = {
        "metadata": {
            "project_name": "loom_finance",
            "source": LOOM_REPO,
            "ref": LOOM_REF,
        },
        "nodes": {
            "source.loom_finance.dim_customers": source_node(
                "dim_customers",
                columns=("customer_id", "customer_name", "customer_tier"),
            ),
            "seed.loom_finance.orders": seed_node_from_csv(
                "orders",
                f"https://raw.githubusercontent.com/{LOOM_REPO}/{LOOM_REF}/projects/finance_project/seeds/orders.csv",
            ),
            "model.loom_finance.fct_revenue_by_customer": model_node(
                "fct_revenue_by_customer",
                compiled_code=compiled_sql,
                depends_on=(
                    "source.loom_finance.dim_customers",
                    "seed.loom_finance.orders",
                ),
                columns=(
                    "customer_id",
                    "customer_name",
                    "customer_tier",
                    "total_revenue",
                ),
            ),
        },
        "child_map": {
            "source.loom_finance.dim_customers": [
                "model.loom_finance.fct_revenue_by_customer"
            ],
            "seed.loom_finance.orders": ["model.loom_finance.fct_revenue_by_customer"],
            "model.loom_finance.fct_revenue_by_customer": [],
        },
    }
    write_fixture_json("loom_finance", payload)


def build_loom_marketing_fixture() -> None:
    compiled_sql = compile_loom_sql(
        fetch_text(
            f"https://raw.githubusercontent.com/{LOOM_REPO}/{LOOM_REF}/projects/marketing_project/models/fct_clicks_by_customer.sql"
        )
    )
    payload = {
        "metadata": {
            "project_name": "loom_marketing",
            "source": LOOM_REPO,
            "ref": LOOM_REF,
        },
        "nodes": {
            "source.loom_marketing.dim_customers": source_node(
                "dim_customers",
                columns=("customer_id", "customer_name", "customer_tier"),
            ),
            "seed.loom_marketing.campaign_events": seed_node_from_csv(
                "campaign_events",
                f"https://raw.githubusercontent.com/{LOOM_REPO}/{LOOM_REF}/projects/marketing_project/seeds/campaign_events.csv",
            ),
            "model.loom_marketing.fct_clicks_by_customer": model_node(
                "fct_clicks_by_customer",
                compiled_code=compiled_sql,
                depends_on=(
                    "source.loom_marketing.dim_customers",
                    "seed.loom_marketing.campaign_events",
                ),
                columns=(
                    "customer_id",
                    "customer_name",
                    "customer_tier",
                    "total_clicks",
                ),
            ),
        },
        "child_map": {
            "source.loom_marketing.dim_customers": [
                "model.loom_marketing.fct_clicks_by_customer"
            ],
            "seed.loom_marketing.campaign_events": [
                "model.loom_marketing.fct_clicks_by_customer"
            ],
            "model.loom_marketing.fct_clicks_by_customer": [],
        },
    }
    write_fixture_json("loom_marketing", payload)


def write_fixtures_md() -> None:
    content = """# catalogkit-lineage fixtures

This directory is the single canonical fixture root for `catalogkit-lineage`.

## Provenance

| Fixture | Source repo | Commit SHA | Dialect | Notes |
|---|---|---|---|---|
| `projects/jaffle_shop` | `dbt-labs/jaffle-shop-classic` | `unknown` | `postgres` | Pre-existing vendored slice moved from `examples/jaffle_shop`; contains 5 models and 3 seeds. |
| `projects/sql_folder` | `n/a` | `n/a` | `postgres` | In-repo plain SQL fixture moved from `examples/sql_folder`; contains 3 SQL files. |
| `projects/shopify` | `fivetran/dbt_shopify` | `a970f76a01fff8524540714f9255af8e62c3b985` | `postgres` | Transitive model closure from anchor models in published `docs/manifest.json`; compiled SQL rewritten to local relation names; macro-union source nodes and tmp-model column metadata inferred from staging `fields` CTE projections. |
| `projects/loom_finance` | `p-munhoz/dbt-loom-multi-project-demo` | `fddd6600448f13e8b945958630a1ec0abe959bc3` | `duckdb` | Curated manifest for the finance project using the public cross-project customer dimension and local `orders` seed. |
| `projects/loom_marketing` | `p-munhoz/dbt-loom-multi-project-demo` | `fddd6600448f13e8b945958630a1ec0abe959bc3` | `duckdb` | Curated manifest for the marketing project using the public cross-project customer dimension and local `campaign_events` seed. |
"""
    FIXTURES_MD.write_text(content, encoding="utf-8")


def child_map_from_nodes(nodes: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {unique_id: [] for unique_id in nodes}
    for unique_id, payload in nodes.items():
        for dependency in payload.get("depends_on", {}).get("nodes", []):
            if dependency in children:
                children[dependency].append(unique_id)
    return {key: sorted(value) for key, value in children.items()}


def transitive_model_closure(
    nodes: dict[str, dict[str, Any]],
    *,
    anchor_names: set[str],
) -> set[str]:
    models_by_id = {
        unique_id: payload
        for unique_id, payload in nodes.items()
        if payload.get("resource_type") == "model"
    }
    model_id_by_name = {
        str(payload.get("name")): unique_id
        for unique_id, payload in models_by_id.items()
    }
    missing = sorted(
        anchor_name
        for anchor_name in anchor_names
        if anchor_name not in model_id_by_name
    )
    if missing:
        raise SystemExit(
            f"Shopify anchor models are missing from docs manifest: {missing}"
        )

    selected_ids: set[str] = set()
    queue = [model_id_by_name[anchor_name] for anchor_name in sorted(anchor_names)]
    while queue:
        unique_id = queue.pop(0)
        if unique_id in selected_ids:
            continue
        payload = models_by_id.get(unique_id)
        if payload is None:
            continue
        selected_ids.add(unique_id)
        for dependency_id in payload.get("depends_on", {}).get("nodes", []):
            if dependency_id in models_by_id and dependency_id not in selected_ids:
                queue.append(dependency_id)
    return selected_ids


def write_fixture_json(project_name: str, payload: dict[str, Any]) -> None:
    target_dir = PROJECTS_ROOT / project_name
    staging_root = Path(tempfile.mkdtemp(prefix=f"{project_name}-", dir=PROJECTS_ROOT))
    try:
        manifest_path = staging_root / "manifest.json"
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if target_dir.exists():
            shutil.rmtree(target_dir)
        staging_root.replace(target_dir)
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise


def source_node(name: str, *, columns: tuple[str, ...]) -> dict[str, Any]:
    return {
        "resource_type": "source",
        "name": name,
        "unique_id": f"source.synthetic.{name}",
        "columns": {column_name: {"name": column_name} for column_name in columns},
    }


def seed_node_from_csv(name: str, url: str) -> dict[str, Any]:
    columns = tuple(read_csv_header(url))
    return {
        "resource_type": "seed",
        "name": name,
        "unique_id": f"seed.synthetic.{name}",
        "columns": {column_name: {"name": column_name} for column_name in columns},
    }


def model_node(
    name: str,
    *,
    compiled_code: str,
    depends_on: tuple[str, ...],
    columns: tuple[str, ...],
) -> dict[str, Any]:
    if not compiled_code.strip():
        raise SystemExit(f"Compiled SQL is empty for model {name!r}.")
    return {
        "resource_type": "model",
        "name": name,
        "unique_id": f"model.synthetic.{name}",
        "compiled_code": compiled_code.strip(),
        "depends_on": {"nodes": list(depends_on)},
        "columns": {column_name: {"name": column_name} for column_name in columns},
    }


def rewrite_shopify_compiled_sql(sql: str) -> str:
    if not sql.strip():
        raise SystemExit("Expected non-empty compiled_code in Shopify manifest.")
    return QUOTED_RELATION_PATTERN.sub(lambda match: match.group(1), sql).strip()


def enrich_shopify_closure(selected_nodes: dict[str, dict[str, Any]]) -> None:
    """Attach production-shaped schema metadata for macro sources and tmp models."""
    models_by_name = {node["name"]: node for node in selected_nodes.values()}
    tmp_to_staging: dict[str, str] = {}
    for name, node in models_by_name.items():
        if not name.endswith("_tmp"):
            continue
        staging_name = name.removesuffix("_tmp")
        if staging_name in models_by_name:
            tmp_to_staging[name] = staging_name

    inferred_columns_by_tmp: dict[str, tuple[str, ...]] = {}
    for tmp_name, staging_name in tmp_to_staging.items():
        staging_sql = str(models_by_name[staging_name].get("compiled_code") or "")
        columns = infer_fields_cte_columns(staging_sql)
        if columns:
            inferred_columns_by_tmp[tmp_name] = columns

    for tmp_name, columns in inferred_columns_by_tmp.items():
        models_by_name[tmp_name]["columns"] = columns_to_manifest(columns)

    source_nodes: dict[str, dict[str, Any]] = {}
    for node in models_by_name.values():
        if not str(node.get("name", "")).endswith("_tmp"):
            continue
        tmp_sql = str(node.get("compiled_code") or "")
        match = MACRO_SOURCE_TABLE_PATTERN.search(tmp_sql)
        if match is None:
            continue
        source_name = match.group(1)
        if source_name in models_by_name:
            continue
        columns = inferred_columns_by_tmp.get(node["name"], ())
        if not columns:
            continue
        unique_id = f"source.shopify_slice.{source_name}"
        source_nodes[unique_id] = source_node(source_name, columns=columns)

    selected_nodes.update(source_nodes)


def infer_fields_cte_columns(compiled_sql: str) -> tuple[str, ...]:
    marker = "fields as ("
    lower_sql = compiled_sql.lower()
    start = lower_sql.find(marker)
    if start < 0:
        return ()
    section = compiled_sql[start:]
    end = section.find("\n)\n,")
    if end < 0:
        end = section.find("\n)\n")
    if end < 0:
        return ()
    fields_section = section[:end]
    columns = FIELDS_CTE_ALIAS_PATTERN.findall(fields_section)
    deduped: list[str] = []
    seen: set[str] = set()
    for column_name in columns:
        if column_name in seen:
            continue
        seen.add(column_name)
        deduped.append(column_name)
    return tuple(deduped)


def columns_to_manifest(columns: tuple[str, ...]) -> dict[str, dict[str, str]]:
    return {column_name: {"name": column_name} for column_name in columns}


def compile_loom_sql(sql: str) -> str:
    sql = JINJA_CROSS_PROJECT_REF_PATTERN.sub(lambda match: match.group(2), sql)
    sql = JINJA_REF_PATTERN.sub(lambda match: match.group(1), sql)
    return sql.strip()


def read_csv_header(url: str) -> list[str]:
    reader = csv.reader(io.StringIO(fetch_text(url)))
    return next(reader)


def fetch_json(url: str) -> dict[str, Any]:
    return json.loads(fetch_text(url))


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "catalogkit-lineage-fixture-refresh"},
    )
    with urllib.request.urlopen(request) as response:
        return response.read().decode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
