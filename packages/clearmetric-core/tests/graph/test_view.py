"""GraphView tests."""

from __future__ import annotations

import pytest
from clearmetric.core.errors import GraphError
from clearmetric.core.models import CatalogArtifact, Edge, Node
from clearmetric.graph import GraphView, build_traversal_subgraph, view_of, walk_related


def test_node_raises_for_missing_id():
    view = view_of(
        CatalogArtifact(
            nodes=[Node(id="column:orders.amount", kind="column", name="amount")]
        )
    )
    with pytest.raises(GraphError, match="Unknown node"):
        view.node("column:missing.col")


def test_walk_upstream_matches_derives_from_orientation():
    artifact = CatalogArtifact(
        nodes=[
            Node(id="column:out.col", kind="column", name="col"),
            Node(id="column:in.col", kind="column", name="col"),
        ],
        edges=[
            Edge(
                kind="derives_from",
                source_id="column:out.col",
                target_id="column:in.col",
            )
        ],
    )
    view = GraphView.from_artifact(artifact)
    assert walk_related(
        view, "column:out.col", direction="upstream", edge_kind="derives_from"
    ) == ["column:in.col"]
    assert walk_related(
        view, "column:in.col", direction="downstream", edge_kind="derives_from"
    ) == ["column:out.col"]


def test_build_traversal_subgraph_includes_selection():
    artifact = CatalogArtifact(
        nodes=[
            Node(id="column:a.x", kind="column", name="x"),
            Node(id="column:b.y", kind="column", name="y"),
        ],
        edges=[
            Edge(kind="derives_from", source_id="column:a.x", target_id="column:b.y"),
        ],
    )
    view = view_of(artifact)
    node_ids, edges = build_traversal_subgraph(
        view, "column:a.x", direction="upstream", edge_kind="derives_from"
    )
    assert node_ids == ["column:a.x", "column:b.y"]
    assert len(edges) == 1
