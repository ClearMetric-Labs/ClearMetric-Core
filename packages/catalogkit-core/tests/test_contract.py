from catalogkit.core import CatalogArtifact, Edge, Node, Warning, render_json


def test_artifact_contract_shape_is_stable():
    artifact = CatalogArtifact(
        nodes=[
            Node(
                id="table:analytics.orders",
                kind="table",
                name="orders",
                qualified_name="analytics.orders",
                schema="analytics",
            )
        ],
        edges=[
            Edge(
                kind="depends_on",
                source_id="cte:customer_rollup",
                target_id="table:analytics.orders",
            )
        ],
        warnings=[
            Warning(
                code="unsupported_construct",
                message="example",
            )
        ],
    )

    payload = render_json(artifact)

    assert payload["version"] == "1"
    assert list(payload.keys()) == ["version", "nodes", "edges", "warnings"]
    assert payload["nodes"][0]["schema"] == "analytics"
