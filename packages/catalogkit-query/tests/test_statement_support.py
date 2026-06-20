from __future__ import annotations

import pytest

from catalogkit.query import build_query_map
from catalogkit.query.errors import QueryMapContractError


def test_create_as_select_is_supported():
    query_map = build_query_map(
        """
        CREATE TABLE analytics.customer_totals AS
        SELECT customer_id, SUM(amount) AS total_amount
        FROM analytics.orders
        GROUP BY customer_id
        """,
        dialect="postgres",
    )

    relation_ids = {relation.id for relation in query_map.relations}
    edge_pairs = {(edge.source_id, edge.target_id) for edge in query_map.edges}

    assert "table:analytics.orders" in relation_ids
    assert ("query:root", "table:analytics.orders") in edge_pairs


def test_insert_select_is_supported():
    query_map = build_query_map(
        """
        INSERT INTO analytics.customer_totals
        SELECT customer_id, SUM(amount) AS total_amount
        FROM analytics.orders
        GROUP BY customer_id
        """,
        dialect="postgres",
    )

    relation_ids = {relation.id for relation in query_map.relations}
    edge_pairs = {(edge.source_id, edge.target_id) for edge in query_map.edges}

    assert "table:analytics.orders" in relation_ids
    assert ("query:root", "table:analytics.orders") in edge_pairs


def test_unsupported_statement_fails_loudly():
    with pytest.raises(QueryMapContractError):
        build_query_map("UPDATE analytics.orders SET status = 'closed'", dialect="postgres")
