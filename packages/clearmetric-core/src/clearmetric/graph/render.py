"""Traversal renderers for impact output."""

from __future__ import annotations

from clearmetric.core import CatalogArtifact, TraversalResult

from .subjects import impact_edge_kind
from .traverse import (
    TraversalDirection,
    build_traversal_subgraph,
    downstream_adjacency,
    upstream_adjacency,
)
from .view import view_of


def render_traversal_tree(
    result: TraversalResult,
    artifact: CatalogArtifact,
    *,
    direction: TraversalDirection,
) -> str:
    """Render an upstream or downstream traversal tree."""
    view = view_of(artifact)
    edge_kind = impact_edge_kind(result.selection_id)
    adjacency = (
        upstream_adjacency(view, edge_kind=edge_kind)
        if direction == "upstream"
        else downstream_adjacency(view, edge_kind=edge_kind)
    )
    node_ids, _edges = build_traversal_subgraph(
        view,
        result.selection_id,
        direction=direction,
        edge_kind=edge_kind,
    )
    allowed_nodes = set(node_ids)
    lines = [
        "clearmetric-core",
        f"{direction}: {result.selection}",
        f"selection_id: {result.selection_id}",
        "tree:",
    ]
    _append_tree(
        lines,
        node_id=result.selection_id,
        adjacency=adjacency,
        allowed_nodes=allowed_nodes,
        depth=1,
        seen=set(),
    )
    if result.warnings:
        lines.append("warnings:")
        for warning in result.warnings:
            lines.append(f"  - {warning.code}: {warning.message}")
    return "\n".join(lines)


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


def _append_tree(
    lines: list[str],
    *,
    node_id: str,
    adjacency: dict[str, list[str]],
    allowed_nodes: set[str],
    depth: int,
    seen: set[str],
) -> None:
    indent = "  " * depth
    suffix = " (cycle)" if node_id in seen else ""
    lines.append(f"{indent}- {node_id}{suffix}")
    if node_id in seen:
        return
    next_seen = {node_id, *seen}
    for child_id in adjacency.get(node_id, []):
        if child_id not in allowed_nodes:
            continue
        _append_tree(
            lines,
            node_id=child_id,
            adjacency=adjacency,
            allowed_nodes=allowed_nodes,
            depth=depth + 1,
            seen=next_seen,
        )


def _mermaid_node_name(node_id: str) -> str:
    chars: list[str] = []
    for character in node_id:
        chars.append(character if character.isalnum() else "_")
    if not chars:
        return "node"
    if chars[0].isdigit():
        chars.insert(0, "_")
    return "".join(chars)
