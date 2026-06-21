from __future__ import annotations

import json
from pathlib import Path

import pytest
from catalogkit.lineage import build_catalog_artifact, build_lineage_map, render_json
from catalogkit.lineage.coverage import (
    ColumnResolution,
    classify_column,
    find_bogus_source_leaves,
    find_silent_columns,
)
from catalogkit.lineage.loaders import load_project

from .ground_truth import FIXTURES_ROOT, jaffle_shop_manifest, project_inputs


@pytest.mark.parametrize(
    ("project_input", "dialect"),
    project_inputs(),
    ids=lambda item: item.name if isinstance(item, Path) else str(item),
)
def test_fixture_has_no_silent_columns_or_bogus_source_leaves(
    project_input: Path,
    dialect: str,
):
    project = load_project(project_input, dialect=dialect)
    artifact = build_catalog_artifact(project_input, dialect=dialect)

    silent_columns = find_silent_columns(artifact, project)
    bogus_source_leaves = find_bogus_source_leaves(artifact, project)

    assert silent_columns == [], f"silent columns: {silent_columns}"
    assert bogus_source_leaves == [], f"bogus source leaves: {bogus_source_leaves}"


@pytest.mark.parametrize(
    ("project_input", "dialect"),
    project_inputs(),
    ids=lambda item: item.name if isinstance(item, Path) else str(item),
)
def test_fixture_lineage_json_is_deterministic(
    project_input: Path,
    dialect: str,
):
    first = json.dumps(
        render_json(build_lineage_map(project_input, dialect=dialect)), sort_keys=True
    )
    second = json.dumps(
        render_json(build_lineage_map(project_input, dialect=dialect)),
        sort_keys=True,
    )
    assert first == second


@pytest.mark.parametrize(
    ("project_input", "dialect"),
    project_inputs(),
    ids=lambda item: item.name if isinstance(item, Path) else str(item),
)
def test_fixture_edges_reference_existing_nodes(
    project_input: Path,
    dialect: str,
):
    artifact = build_catalog_artifact(project_input, dialect=dialect)
    node_ids = {node.id for node in artifact.nodes}
    for edge in artifact.edges:
        assert edge.source_id in node_ids
        assert edge.target_id in node_ids


def test_source_leaf_regression_cases():
    jaffle_manifest = jaffle_shop_manifest()
    shopify_manifest = FIXTURES_ROOT / "projects" / "shopify" / "manifest.json"

    jaffle_project = load_project(jaffle_manifest, dialect="postgres")
    jaffle_artifact = build_catalog_artifact(jaffle_manifest, dialect="postgres")
    shopify_project = load_project(shopify_manifest, dialect="postgres")
    shopify_artifact = build_catalog_artifact(shopify_manifest, dialect="postgres")

    assert (
        classify_column("column:raw_payments.amount", jaffle_artifact, jaffle_project)
        == ColumnResolution.SOURCE_LEAF
    )
    assert (
        classify_column("column:raw_orders.id", jaffle_artifact, jaffle_project)
        == ColumnResolution.SOURCE_LEAF
    )
    assert classify_column(
        "column:shopify__customers.lifetime_total_spent",
        shopify_artifact,
        shopify_project,
    ) in {ColumnResolution.RESOLVED, ColumnResolution.FLAGGED}
    assert (
        classify_column(
            "column:customers.customer_lifetime_value",
            jaffle_artifact,
            jaffle_project,
        )
        != ColumnResolution.SOURCE_LEAF
    )
