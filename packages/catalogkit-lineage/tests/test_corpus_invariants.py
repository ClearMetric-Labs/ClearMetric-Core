from __future__ import annotations

import json

import pytest
from catalogkit.lineage.build import build_lineage_map_from_project
from catalogkit.lineage.coverage import (
    ColumnResolution,
    classify_column,
    coverage_summary,
    find_bogus_source_leaves,
    find_silent_columns,
)
from catalogkit.lineage.loaders import load_project
from catalogkit.lineage.render.json import render_json

from .ground_truth import (
    FIXTURES_ROOT,
    coverage_baselines,
    load_built_fixture,
    project_fixture_input,
    project_inputs,
)

PROJECT_FIXTURES = project_inputs()
_COVERAGE_KEYS = (
    "total",
    "resolved",
    "flagged",
    "source_leaf",
    "silent",
    "bogus_source_leaves",
    "warning_counts",
)


@pytest.mark.parametrize(
    ("project_input", "dialect"),
    PROJECT_FIXTURES,
    ids=[
        pair[0].parent.name if pair[0].name == "manifest.json" else pair[0].name
        for pair in PROJECT_FIXTURES
    ],
)
def test_fixture_corpus_invariants(project_input, dialect: str):
    project_dir = (
        project_input.parent if project_input.name == "manifest.json" else project_input
    )
    project, artifact = load_built_fixture(project_input, dialect)

    silent_columns = find_silent_columns(artifact, project)
    bogus_source_leaves = find_bogus_source_leaves(artifact, project)
    assert silent_columns == [], f"silent columns: {silent_columns}"
    assert bogus_source_leaves == [], f"bogus source leaves: {bogus_source_leaves}"

    node_ids = {node.id for node in artifact.nodes}
    for edge in artifact.edges:
        assert edge.source_id in node_ids
        assert edge.target_id in node_ids

    baseline = coverage_baselines()[project_dir.name]
    summary = coverage_summary(artifact, project)
    for key in _COVERAGE_KEYS:
        assert summary[key] == baseline[key], (
            f"{project_dir.name}.{key}: expected {baseline[key]!r}, got {summary[key]!r}"
        )


def test_manifest_fixture_build_is_deterministic():
    manifest = project_fixture_input(FIXTURES_ROOT / "projects" / "jaffle_shop")
    project = load_project(manifest, dialect="postgres")
    first = json.dumps(
        render_json(build_lineage_map_from_project(project, dialect="postgres")),
        sort_keys=True,
    )
    second = json.dumps(
        render_json(build_lineage_map_from_project(project, dialect="postgres")),
        sort_keys=True,
    )
    assert first == second


def test_source_leaf_regression_cases():
    jaffle_manifest = project_fixture_input(FIXTURES_ROOT / "projects" / "jaffle_shop")
    shopify_manifest = project_fixture_input(FIXTURES_ROOT / "projects" / "shopify")

    jaffle_project, jaffle_artifact = load_built_fixture(jaffle_manifest, "postgres")
    shopify_project, shopify_artifact = load_built_fixture(shopify_manifest, "postgres")

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
