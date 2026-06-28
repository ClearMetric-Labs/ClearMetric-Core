"""Link metric and query depends_on references into graph edges."""

from __future__ import annotations

from clearmetric.core.contracts import contract_dependency_violations, contract_for_node
from clearmetric.core.errors import CompilerError
from clearmetric.core.models import CatalogArtifact, Edge


def link_contract_dependencies(artifact: CatalogArtifact) -> CatalogArtifact:
    """Add depends_on edges from metric/query contract aspects."""
    node_ids = {node.id for node in artifact.nodes}
    existing = {(edge.kind, edge.source_id, edge.target_id) for edge in artifact.edges}
    new_edges: list[Edge] = []
    violations = contract_dependency_violations(artifact, node_ids=node_ids)

    for node in artifact.nodes:
        contract = contract_for_node(node)
        if contract is None:
            continue

        for dep_id in contract.depends_on:
            if dep_id not in node_ids:
                continue
            key = ("depends_on", dep_id, node.id)
            if key not in existing:
                new_edges.append(
                    Edge(
                        kind="depends_on",
                        source_id=dep_id,
                        target_id=node.id,
                        label="depends_on",
                    )
                )
                existing.add(key)

    if violations:
        raise CompilerError("; ".join(violations))

    if not new_edges:
        return artifact

    return CatalogArtifact(
        version=artifact.version,
        nodes=artifact.nodes,
        edges=[*artifact.edges, *new_edges],
        warnings=artifact.warnings,
    )
