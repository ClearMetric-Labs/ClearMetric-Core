"""Artifact merge utilities."""

from __future__ import annotations

from typing import Any

from .errors import MergeConflictError
from .models import CatalogArtifact, Edge, Evidence, Node, Warning


def merge(*artifacts: CatalogArtifact) -> CatalogArtifact:
    """Merge multiple artifacts under the shared catalogkit-core rules."""
    merged_nodes: dict[str, Node] = {}
    merged_edges: dict[tuple[str, str, str], Edge] = {}
    merged_warnings: dict[tuple[str, str, str | None], Warning] = {}

    for artifact in artifacts:
        for node in artifact.nodes:
            existing = merged_nodes.get(node.id)
            merged_nodes[node.id] = node if existing is None else _merge_node(existing, node)

        for edge in artifact.edges:
            key = (edge.kind, edge.source_id, edge.target_id)
            existing = merged_edges.get(key)
            merged_edges[key] = edge if existing is None else _merge_edge(existing, edge)

        for warning in artifact.warnings:
            merged_warnings[(warning.code, warning.message, warning.location)] = warning

    return CatalogArtifact(
        version=_merge_versions([artifact.version for artifact in artifacts]),
        nodes=sorted(merged_nodes.values(), key=lambda node: node.id),
        edges=sorted(
            merged_edges.values(),
            key=lambda edge: (edge.kind, edge.source_id, edge.target_id),
        ),
        warnings=sorted(
            merged_warnings.values(),
            key=lambda warning: (warning.code, warning.message, warning.location or ""),
        ),
    )


def _merge_versions(versions: list[str]) -> str:
    unique_versions = {version for version in versions if version}
    if not unique_versions:
        return "1"
    if len(unique_versions) != 1:
        raise MergeConflictError(
            f"Cannot merge artifacts with different schema versions: {sorted(unique_versions)}"
        )
    return unique_versions.pop()


def _merge_node(left: Node, right: Node) -> Node:
    return Node(
        id=left.id,
        kind=_merge_scalar("node.kind", left.kind, right.kind, left.id),
        name=_merge_scalar("node.name", left.name, right.name, left.id),
        qualified_name=_merge_optional(
            "node.qualified_name",
            left.qualified_name,
            right.qualified_name,
            left.id,
        ),
        schema=_merge_optional("node.schema", left.schema_name, right.schema_name, left.id),
        evidence=_merge_evidence(left.evidence, right.evidence),
    )


def _merge_edge(left: Edge, right: Edge) -> Edge:
    return Edge(
        kind=left.kind,
        source_id=left.source_id,
        target_id=left.target_id,
        label=_merge_optional("edge.label", left.label, right.label, left.source_id),
        confidence=_merge_scalar(
            "edge.confidence",
            left.confidence,
            right.confidence,
            f"{left.kind}:{left.source_id}->{left.target_id}",
        ),
        evidence=_merge_evidence(left.evidence, right.evidence),
    )


def _merge_scalar(field_name: str, left: Any, right: Any, owner: str) -> Any:
    if left != right:
        raise MergeConflictError(
            f"Conflicting {field_name} values for {owner!r}: {left!r} != {right!r}"
        )
    return left


def _merge_optional(field_name: str, left: Any, right: Any, owner: str) -> Any:
    if left in (None, ""):
        return right
    if right in (None, ""):
        return left
    if left != right:
        raise MergeConflictError(
            f"Conflicting {field_name} values for {owner!r}: {left!r} != {right!r}"
        )
    return left


def _merge_evidence(left: list[Evidence], right: list[Evidence]) -> list[Evidence]:
    seen: set[tuple[str | None, str | None, str | None, str]] = set()
    merged: list[Evidence] = []

    for evidence in [*left, *right]:
        key = (
            evidence.file,
            evidence.location,
            evidence.expression,
            evidence.confidence,
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(evidence)

    return merged
