"""Adversarial policy evaluation tests."""

from __future__ import annotations

from unittest.mock import patch

from clearmetric.core.models import CatalogArtifact, Node
from clearmetric.policy import evaluate_node
from clearmetric.policy.models import PolicyRule, PolicyRulesFile, PolicySelector
from clearmetric.projection import project_consumer_catalog, project_for_emit


def _node(**kwargs) -> Node:
    defaults = {"id": "column:orders.email", "kind": "column", "name": "email"}
    defaults.update(kwargs)
    return Node(**defaults)


def test_deny_beats_allow_on_same_node():
    rules = PolicyRulesFile(
        rules=[
            PolicyRule(
                id="allow-columns",
                kind="rbac",
                identity="analyst",
                effect="allow",
                selector=PolicySelector(kind="column"),
            ),
            PolicyRule(
                id="deny-email",
                kind="rbac",
                identity="analyst",
                effect="deny",
                selector=PolicySelector(kind="column"),
            ),
        ]
    )
    node = _node()
    assert evaluate_node(node=node, identity="analyst", rules=rules) == "deny"


def test_evaluator_exception_denies():
    rules = PolicyRulesFile(rules=[])
    node = _node()
    with patch(
        "clearmetric.policy.evaluate._rule_matches",
        side_effect=RuntimeError("boom"),
    ):
        assert evaluate_node(node=node, identity="analyst", rules=rules) == "deny"


def test_empty_rules_denies_by_default():
    node = _node()
    assert evaluate_node(node=node, identity="analyst", rules=PolicyRulesFile()) == "deny"


def test_wrong_identity_denies():
    rules = PolicyRulesFile(
        rules=[
            PolicyRule(
                id="viewer-tables",
                kind="rbac",
                identity="viewer",
                effect="allow",
                selector=PolicySelector(kind="table"),
            )
        ]
    )
    node = Node(id="table:orders", kind="table", name="orders")
    assert evaluate_node(node=node, identity="analyst", rules=rules) == "deny"


def test_mask_strips_sensitive_aspects_in_consumer_catalog():
    artifact = CatalogArtifact(
        nodes=[
            Node(
                id="column:orders.email",
                kind="column",
                name="email",
                aspects={
                    "classification": "pii",
                    "policy_refs": ["policy/email"],
                    "ai_behavior": {"allowed": False},
                },
            )
        ]
    )
    rules = PolicyRulesFile(
        rules=[
            PolicyRule(
                id="mask-pii",
                kind="masking",
                identity="analyst",
                effect="mask",
                selector=PolicySelector(kind="column"),
            )
        ]
    )
    projected = project_consumer_catalog(
        artifact, identity="analyst", rules=rules
    )
    assert len(projected.nodes) == 1
    aspects = projected.nodes[0].aspects or {}
    assert "classification" not in aspects
    assert aspects.get("_policy_masked") is True


def test_rls_filter_excludes_denied_nodes_from_projection():
    artifact = CatalogArtifact(
        nodes=[
            Node(id="table:orders", kind="table", name="orders"),
            Node(id="column:orders.email", kind="column", name="email"),
        ]
    )
    rules = PolicyRulesFile(
        rules=[
            PolicyRule(
                id="deny-email",
                kind="rls",
                identity="viewer",
                effect="deny",
                selector=PolicySelector(kind="column"),
            ),
            PolicyRule(
                id="allow-tables",
                kind="rbac",
                identity="viewer",
                effect="allow",
                selector=PolicySelector(kind="table"),
            ),
        ]
    )
    projected = project_for_emit(artifact, identity="viewer", rules=rules)
    assert [node.id for node in projected.nodes] == ["table:orders"]
