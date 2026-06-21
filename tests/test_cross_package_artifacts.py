from __future__ import annotations

from pathlib import Path

from catalogkit.core import merge
from catalogkit.lineage import build_catalog_artifact
from catalogkit.query import build_catalog_artifact as build_query_catalog_artifact

REPO_ROOT = Path(__file__).resolve().parents[1]
JAFFLE_MANIFEST = (
    REPO_ROOT
    / "packages"
    / "catalogkit-lineage"
    / "tests"
    / "fixtures"
    / "projects"
    / "jaffle_shop"
    / "manifest.json"
)


def test_lineage_and_query_artifacts_merge_on_shared_ids():
    customers_sql = (JAFFLE_MANIFEST.parent / "compiled" / "customers.sql").read_text(
        encoding="utf-8"
    )

    lineage_artifact = build_catalog_artifact(JAFFLE_MANIFEST, dialect="postgres")
    query_artifact = build_query_catalog_artifact(customers_sql, dialect="postgres")
    merged = merge(lineage_artifact, query_artifact)

    stg_orders_nodes = [node for node in merged.nodes if node.id == "table:stg_orders"]
    raw_payments_nodes = [
        node for node in merged.nodes if node.id == "table:raw_payments"
    ]

    assert len(stg_orders_nodes) == 1
    assert len(raw_payments_nodes) == 1
