"""Contract node validation tests."""

from __future__ import annotations

import pytest
from clearmetric.compiler.contracts import validate_contract_nodes
from clearmetric.core.errors import CompilerError
from clearmetric.core.models import CatalogArtifact, Node


def test_metric_node_requires_contract_aspect():
    artifact = CatalogArtifact(
        nodes=[Node(id="metric:revenue", kind="metric", name="revenue")]
    )
    with pytest.raises(CompilerError, match="aspects.metric"):
        validate_contract_nodes(artifact)


def test_valid_metric_and_query_nodes_pass():
    artifact = CatalogArtifact(
        nodes=[
            Node(
                id="column:orders.amount",
                kind="column",
                name="amount",
                qualified_name="orders.amount",
            ),
            Node(
                id="metric:revenue",
                kind="metric",
                name="revenue",
                aspects={
                    "metric": {
                        "formula": "sum(orders.amount)",
                        "depends_on": ["column:orders.amount"],
                    }
                },
            ),
            Node(
                id="query:top_orders",
                kind="query",
                name="top_orders",
                aspects={
                    "query": {
                        "sql": "SELECT amount FROM orders",
                        "depends_on": ["column:orders.amount"],
                    }
                },
            ),
        ]
    )
    validate_contract_nodes(artifact)
