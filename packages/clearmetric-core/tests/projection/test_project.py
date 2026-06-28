"""Policy-gated projection tests."""

from __future__ import annotations

from clearmetric.core.models import CatalogArtifact, Edge, Node
from clearmetric.policy.models import PolicyRule, PolicyRulesFile, PolicySelector
from clearmetric.projection import project_for_emit


def test_project_for_emit_filters_denied_nodes():
    artifact = CatalogArtifact(
        nodes=[
            Node(
                id="table:orders", kind="table", name="orders", qualified_name="orders"
            ),
            Node(
                id="column:orders.amount",
                kind="column",
                name="amount",
                qualified_name="orders.amount",
            ),
        ],
        edges=[
            Edge(
                kind="derives_from",
                source_id="table:orders",
                target_id="column:orders.amount",
                label="derives_from",
            )
        ],
    )
    rules = PolicyRulesFile(
        rules=[
            PolicyRule(
                id="tables-only",
                kind="rbac",
                identity="viewer",
                effect="allow",
                selector=PolicySelector(kind="table"),
            )
        ]
    )
    projected = project_for_emit(artifact, identity="viewer", rules=rules)
    assert [node.id for node in projected.nodes] == ["table:orders"]
    assert projected.edges == []
