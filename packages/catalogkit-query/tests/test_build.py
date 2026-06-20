from pathlib import Path

from catalogkit.query import build_catalog_artifact, build_query_map, render_json


def _read_example(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / "examples" / name).read_text(encoding="utf-8")


def test_catalogkit_query_emits_legacy_public_shape():
    query_map = build_query_map(_read_example("ugly_real_world.sql"), dialect="postgres")
    payload = render_json(query_map)

    relation_ids = {relation["id"] for relation in payload["relations"]}
    edge_pairs = {(edge["source_id"], edge["target_id"]) for edge in payload["edges"]}
    warning_codes = {warning["code"] for warning in payload["warnings"]}

    assert payload["version"] == "1"
    assert payload["summary"]["dialect"] == "postgres"
    assert "table:analytics.orders" in relation_ids
    assert "table:analytics.customers" in relation_ids
    assert "cte:base_orders" in relation_ids
    assert "cte:customer_rollup" in relation_ids
    assert "cte:ranked_customers" in relation_ids
    assert ("cte:base_orders", "table:analytics.orders") in edge_pairs
    assert ("query:root", "cte:ranked_customers") in edge_pairs
    assert "select_star" in warning_codes


def test_catalogkit_query_preserves_relation_usage_details():
    query_map = build_query_map(
        """
        WITH temp AS (
            SELECT *
            FROM raw.orders o
        )
        SELECT *
        FROM temp t
        """,
        dialect="postgres",
    )

    usage_details = {
        (usage.relation_id, usage.alias, usage.context, usage.sql)
        for usage in query_map.relation_usages
    }

    assert ("table:raw.orders", "o", "cte_body", "raw.orders AS o") in usage_details
    assert ("cte:temp", "t", "from", "temp AS t") in usage_details


def test_catalogkit_query_can_build_shared_catalog_artifact():
    artifact = build_catalog_artifact(_read_example("simple.sql"), dialect="postgres")

    node_ids = {node.id for node in artifact.nodes}

    assert artifact.version == "1"
    assert "table:public.customers" in node_ids
    assert "cte:active_customers" in node_ids
