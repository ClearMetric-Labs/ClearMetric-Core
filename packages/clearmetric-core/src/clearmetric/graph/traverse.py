"""Graph traversal over canonical edge kinds."""

from __future__ import annotations

from typing import Literal

from clearmetric.core.models import Edge

from .view import GraphView

TraversalDirection = Literal["upstream", "downstream"]


def _adjacency(
    view: GraphView,
    *,
    direction: TraversalDirection,
    edge_kind: str,
) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {}
    for edge in view.edges(kind=edge_kind):
        if direction == "upstream":
            adjacency.setdefault(edge.source_id, []).append(edge.target_id)
        else:
            adjacency.setdefault(edge.target_id, []).append(edge.source_id)
    return adjacency


def _edges_by_pair(
    view: GraphView,
    *,
    direction: TraversalDirection,
    edge_kind: str,
) -> dict[tuple[str, str], Edge]:
    pairs: dict[tuple[str, str], Edge] = {}
    for edge in view.edges(kind=edge_kind):
        if direction == "upstream":
            key = (edge.source_id, edge.target_id)
        else:
            key = (edge.target_id, edge.source_id)
        pairs[key] = edge
    return pairs


def neighbors(
    view: GraphView,
    node_id: str,
    *,
    direction: TraversalDirection,
    edge_kind: str,
) -> list[str]:
    adjacency = _adjacency(view, direction=direction, edge_kind=edge_kind)
    return list(adjacency.get(node_id, ()))


def walk_related(
    view: GraphView,
    start_id: str,
    *,
    direction: TraversalDirection,
    edge_kind: str,
) -> list[str]:
    adjacency = _adjacency(view, direction=direction, edge_kind=edge_kind)
    visited: set[str] = set()
    stack = [start_id]
    related: list[str] = []
    while stack:
        current = stack.pop()
        for adjacent_id in adjacency.get(current, ()):
            if adjacent_id in visited:
                continue
            visited.add(adjacent_id)
            related.append(adjacent_id)
            stack.append(adjacent_id)
    return related


def traverse(
    view: GraphView,
    start_id: str,
    *,
    direction: TraversalDirection,
    edge_kind: str,
) -> tuple[list[str], list[Edge]]:
    node_ids, edges = build_traversal_subgraph(
        view,
        start_id,
        direction=direction,
        edge_kind=edge_kind,
    )
    return node_ids, edges


def downstream_adjacency(
    view: GraphView,
    *,
    edge_kind: str = "derives_from",
) -> dict[str, list[str]]:
    return _adjacency(view, direction="downstream", edge_kind=edge_kind)


def upstream_adjacency(
    view: GraphView,
    *,
    edge_kind: str = "derives_from",
) -> dict[str, list[str]]:
    return _adjacency(view, direction="upstream", edge_kind=edge_kind)


def build_traversal_subgraph(
    view: GraphView,
    selection_id: str,
    *,
    direction: TraversalDirection,
    edge_kind: str,
) -> tuple[list[str], list[Edge]]:
    adjacency = _adjacency(view, direction=direction, edge_kind=edge_kind)
    edges_by_pair = _edges_by_pair(view, direction=direction, edge_kind=edge_kind)
    visited_nodes = {selection_id}
    visited_edges: list[Edge] = []
    stack = [selection_id]
    while stack:
        current = stack.pop()
        for adjacent_id in adjacency.get(current, ()):
            pair = (current, adjacent_id)
            edge = edges_by_pair[pair]
            if edge not in visited_edges:
                visited_edges.append(edge)
            if adjacent_id in visited_nodes:
                continue
            visited_nodes.add(adjacent_id)
            stack.append(adjacent_id)
    related = walk_related(
        view,
        selection_id,
        direction=direction,
        edge_kind=edge_kind,
    )
    return [selection_id, *related], visited_edges
