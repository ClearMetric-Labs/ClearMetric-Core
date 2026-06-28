"""Hygiene checks for contract nodes."""

from __future__ import annotations

from clearmetric.core.contracts import MetricContract, contract_for_node
from clearmetric.graph import GraphView

from .models import Finding


def check_duplicate_formula(view: GraphView) -> list[Finding]:
    formulas: dict[str, str] = {}
    findings: list[Finding] = []
    for node in view.nodes():
        contract = contract_for_node(node)
        if not isinstance(contract, MetricContract):
            continue
        normalized = contract.formula.strip().lower()
        if not normalized:
            continue
        existing = formulas.get(normalized)
        if existing is not None and existing != node.id:
            findings.append(
                Finding(
                    check_id="check.duplicate_formula",
                    node_id=node.id,
                    severity=None,
                    message=(
                        f"Metric {node.id!r} duplicates formula already used by {existing!r}"
                    ),
                    tier="warn",
                )
            )
        else:
            formulas[normalized] = node.id
    return findings
