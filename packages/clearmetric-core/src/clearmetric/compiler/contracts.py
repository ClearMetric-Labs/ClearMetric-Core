"""Contract node validation."""

from __future__ import annotations

from clearmetric.core.contracts import (
    MetricContract,
    QueryContract,
    contract_dependency_violations,
    contract_for_node,
)
from clearmetric.core.errors import CompilerError
from clearmetric.core.models import CatalogArtifact


def validate_contract_nodes(artifact: CatalogArtifact) -> None:
    """Validate metric and query contract aspects on graph nodes."""
    violations: list[str] = []
    node_ids = {node.id for node in artifact.nodes}

    for node in artifact.nodes:
        contract = contract_for_node(node)
        if contract is None:
            if node.kind in {"metric", "query"}:
                aspect = "metric" if node.kind == "metric" else "query"
                violations.append(f"{node.id} {node.kind} node missing aspects.{aspect}")
            continue

        if isinstance(contract, MetricContract):
            if not contract.formula.strip():
                violations.append(f"{node.id} metric contract requires non-empty formula")
        elif isinstance(contract, QueryContract):
            if not contract.sql.strip():
                violations.append(f"{node.id} query contract requires non-empty sql")

    violations.extend(
        contract_dependency_violations(artifact, node_ids=node_ids)
    )

    if violations:
        raise CompilerError("; ".join(violations))
