"""Policy evaluation."""

from __future__ import annotations

from clearmetric.core.models import Node

from .models import PolicyDecision, PolicyRule, PolicyRulesFile


def _rule_matches(*, rule: PolicyRule, node: Node, identity: str) -> bool:
    if rule.identity != identity:
        return False
    if rule.selector is not None and rule.selector.kind is not None:
        if rule.selector.kind != node.kind:
            return False
    return True


def evaluate_node(
    *, node: Node, identity: str, rules: PolicyRulesFile
) -> PolicyDecision:
    """Evaluate policy for one node. Deny beats allow; exceptions fail closed."""
    try:
        matching = [
            rule
            for rule in rules.rules
            if _rule_matches(rule=rule, node=node, identity=identity)
        ]
        if not matching:
            return "deny"

        if any(rule.effect == "deny" for rule in matching):
            return "deny"

        if any(rule.kind == "masking" or rule.effect == "mask" for rule in matching):
            return "mask"

        if any(rule.effect == "allow" for rule in matching):
            return "allow"

        return "deny"
    except Exception:
        return "deny"
