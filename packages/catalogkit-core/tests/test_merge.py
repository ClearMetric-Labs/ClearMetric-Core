import pytest

from catalogkit.core import CatalogArtifact, Edge, Evidence, MergeConflictError, Node, Warning, merge


def test_merge_unions_node_and_edge_evidence():
    left = CatalogArtifact(
        nodes=[
            Node(
                id="table:analytics.orders",
                kind="table",
                name="orders",
                qualified_name="analytics.orders",
                schema="analytics",
                evidence=[Evidence(expression="analytics.orders", confidence="high")],
            )
        ],
        edges=[
            Edge(
                kind="depends_on",
                source_id="query:root",
                target_id="table:analytics.orders",
                evidence=[Evidence(expression="FROM analytics.orders", confidence="high")],
            )
        ],
        warnings=[Warning(code="select_star", message="star", location="*")],
    )
    right = CatalogArtifact(
        nodes=[
            Node(
                id="table:analytics.orders",
                kind="table",
                name="orders",
                qualified_name="analytics.orders",
                schema="analytics",
                evidence=[Evidence(expression='"analytics"."orders"', confidence="medium")],
            )
        ],
        edges=[
            Edge(
                kind="depends_on",
                source_id="query:root",
                target_id="table:analytics.orders",
                evidence=[Evidence(expression='"analytics"."orders"', confidence="medium")],
            )
        ],
        warnings=[Warning(code="select_star", message="star", location="*")],
    )

    merged = merge(left, right)

    assert len(merged.nodes) == 1
    assert len(merged.nodes[0].evidence) == 2
    assert len(merged.edges) == 1
    assert len(merged.edges[0].evidence) == 2
    assert len(merged.warnings) == 1


def test_merge_fails_on_conflicting_node_attributes():
    left = CatalogArtifact(
        nodes=[
            Node(
                id="table:analytics.orders",
                kind="table",
                name="orders",
                qualified_name="analytics.orders",
            )
        ]
    )
    right = CatalogArtifact(
        nodes=[
            Node(
                id="table:analytics.orders",
                kind="table",
                name="purchase_orders",
                qualified_name="analytics.orders",
            )
        ]
    )

    with pytest.raises(MergeConflictError):
        merge(left, right)


def test_merge_dedupes_edges_by_kind_source_target():
    left = CatalogArtifact(
        edges=[
            Edge(
                kind="depends_on",
                source_id="cte:customer_rollup",
                target_id="table:analytics.orders",
                label="depends_on",
            )
        ]
    )
    right = CatalogArtifact(
        edges=[
            Edge(
                kind="depends_on",
                source_id="cte:customer_rollup",
                target_id="table:analytics.orders",
                label="depends_on",
            )
        ]
    )

    merged = merge(left, right)

    assert len(merged.edges) == 1
