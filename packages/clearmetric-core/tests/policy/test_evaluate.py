from __future__ import annotations

from clearmetric.core.models import Node
from clearmetric.policy import evaluate_node
from clearmetric.policy.models import PolicyRule, PolicyRulesFile, PolicySelector


def test_matching_allow_rule():
    rules = PolicyRulesFile(
        rules=[
            PolicyRule(
                id="analyst-tables",
                kind="rbac",
                identity="analyst@example.com",
                effect="allow",
                selector=PolicySelector(kind="table"),
            )
        ]
    )
    node = Node(id="table:orders", kind="table", name="orders", qualified_name="orders")
    assert (
        evaluate_node(node=node, identity="analyst@example.com", rules=rules) == "allow"
    )


def test_zero_matching_rules_denies():
    rules = PolicyRulesFile(rules=[])
    node = Node(id="table:orders", kind="table", name="orders", qualified_name="orders")
    assert (
        evaluate_node(node=node, identity="analyst@example.com", rules=rules) == "deny"
    )
