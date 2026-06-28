"""Mermaid renderer for clearmetric-core traversals."""

from __future__ import annotations

from clearmetric.core import CatalogArtifact
from clearmetric.graph import (
    TraversalDirection,
    build_traversal_subgraph,
    impact_edge_kind,
    view_of,
)


def render_traversal_mermaid(
    selection_id: str,
    artifact: CatalogArtifact,
    *,
    direction: TraversalDirection,
) -> str:
    view = view_of(artifact)
    edge_kind = impact_edge_kind(selection_id)
    node_ids, edges = build_traversal_subgraph(
        view,
        selection_id,
        direction=direction,
        edge_kind=edge_kind,
    )
    lines = ["flowchart TD"]
    for node_id in node_ids:
        node_name = _mermaid_node_name(node_id)
        lines.append(f'  {node_name}["{node_id}"]')
    for edge in edges:
        source_name = _mermaid_node_name(edge.source_id)
        target_name = _mermaid_node_name(edge.target_id)
        lines.append(f"  {source_name} --> {target_name}")
    return "\n".join(lines)


def _mermaid_node_name(node_id: str) -> str:
    chars: list[str] = []
    for character in node_id:
        chars.append(character if character.isalnum() else "_")
    if not chars:
        return "node"
    if chars[0].isdigit():
        chars.insert(0, "_")
    return "".join(chars)
