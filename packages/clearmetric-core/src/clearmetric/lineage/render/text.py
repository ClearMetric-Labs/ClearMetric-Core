"""Text renderer for clearmetric-core."""

from __future__ import annotations

from clearmetric.core import CatalogArtifact
from clearmetric.graph import (
    TraversalDirection,
    build_traversal_subgraph,
    downstream_adjacency,
    impact_edge_kind,
    upstream_adjacency,
    view_of,
)

from ..models import LineageMap, TraversalResult


def render_text(lineage_map: LineageMap) -> str:
    """Render the public clearmetric-core artifact for human reading."""
    lines: list[str] = []
    summary = lineage_map.summary
    lines.append("clearmetric-core")
    lines.append(f"dialect: {summary.dialect}")
    lines.append(f"input_kind: {summary.input_kind}")
    lines.append(f"dataset_count: {summary.dataset_count}")
    lines.append(f"root_dataset_count: {summary.root_dataset_count}")
    lines.append(f"column_count: {summary.column_count}")
    lines.append("")
    lines.append("nodes:")
    for node in lineage_map.nodes:
        display_name = node.qualified_name or node.name
        lines.append(f"  - [{node.kind}] {display_name}")
    lines.append("")
    lines.append("edges:")
    for edge in lineage_map.edges:
        lines.append(f"  - [{edge.kind}] {edge.source_id} -> {edge.target_id}")
    if lineage_map.warnings:
        lines.append("")
        lines.append("warnings:")
        for warning in lineage_map.warnings:
            location = f" [{warning.location}]" if warning.location else ""
            lines.append(f"  - {warning.code}: {warning.message}{location}")
    return "\n".join(lines)


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
