"""Graph helpers for catalogkit-lineage traversals."""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Literal

from catalogkit.core import CatalogArtifact, Edge, Warning, split_qualified_identifier

TraversalDirection = Literal["upstream", "downstream"]


def derives_from_edges(artifact: CatalogArtifact) -> list[Edge]:
    return [edge for edge in artifact.edges if edge.kind == "derives_from"]


def derives_from_counts_by_source_dataset(edges: Iterable[Edge]) -> dict[str, int]:
    """Count value-lineage edges grouped by output (source) dataset name."""
    counts: Counter[str] = Counter()
    for edge in edges:
        if edge.kind != "derives_from":
            continue
        dataset_name, _column_name = column_selection_from_id(edge.source_id)
        counts[dataset_name] += 1
    return dict(counts)


def upstream_adjacency(artifact: CatalogArtifact) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {}
    for edge in derives_from_edges(artifact):
        adjacency.setdefault(edge.source_id, []).append(edge.target_id)
    return adjacency


def downstream_adjacency(artifact: CatalogArtifact) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {}
    for edge in derives_from_edges(artifact):
        adjacency.setdefault(edge.target_id, []).append(edge.source_id)
    return adjacency


def walk_upstream(selection_id: str, artifact: CatalogArtifact) -> list[str]:
    return _walk(selection_id, adjacency=upstream_adjacency(artifact))


def walk_downstream(selection_id: str, artifact: CatalogArtifact) -> list[str]:
    return _walk(selection_id, adjacency=downstream_adjacency(artifact))


def warnings_for_subject(
    artifact: CatalogArtifact,
    subject_id: str,
    *,
    dataset_name: str | None = None,
) -> list[Warning]:
    matching = [
        warning for warning in artifact.warnings if warning.subject_id == subject_id
    ]
    if matching or dataset_name is None:
        return matching
    return [
        warning
        for warning in artifact.warnings
        if warning.subject_id is None
        and dataset_from_location(warning.location) == dataset_name
    ]


def column_selection_from_id(node_id: str) -> tuple[str, str]:
    if not node_id.startswith("column:"):
        raise ValueError(f"Expected column node id, got {node_id!r}")
    qualified_name = node_id[len("column:") :]
    parts = split_qualified_identifier(qualified_name)
    if len(parts) < 2:
        raise ValueError(f"Expected qualified column id, got {node_id!r}")
    return ".".join(parts[:-1]), parts[-1]


def build_traversal_subgraph(
    selection_id: str,
    artifact: CatalogArtifact,
    *,
    direction: TraversalDirection,
) -> tuple[list[str], list[Edge]]:
    adjacency = (
        upstream_adjacency(artifact)
        if direction == "upstream"
        else downstream_adjacency(artifact)
    )
    visited_nodes = {selection_id}
    visited_edges: list[Edge] = []
    edges_by_pair = _edges_by_pair(artifact, direction=direction)
    stack = [selection_id]
    while stack:
        current = stack.pop()
        for adjacent_id in adjacency.get(current, []):
            pair = (
                (current, adjacent_id)
                if direction == "upstream"
                else (current, adjacent_id)
            )
            edge = edges_by_pair[pair]
            if edge not in visited_edges:
                visited_edges.append(edge)
            if adjacent_id in visited_nodes:
                continue
            visited_nodes.add(adjacent_id)
            stack.append(adjacent_id)
    return [selection_id, *_walk(selection_id, adjacency=adjacency)], visited_edges


def _walk(selection_id: str, *, adjacency: dict[str, list[str]]) -> list[str]:
    visited: set[str] = set()
    stack = [selection_id]
    related: list[str] = []
    while stack:
        current = stack.pop()
        for adjacent_id in adjacency.get(current, []):
            if adjacent_id in visited:
                continue
            visited.add(adjacent_id)
            related.append(adjacent_id)
            stack.append(adjacent_id)
    return related


def _edges_by_pair(
    artifact: CatalogArtifact, *, direction: TraversalDirection
) -> dict[tuple[str, str], Edge]:
    pairs: dict[tuple[str, str], Edge] = {}
    for edge in derives_from_edges(artifact):
        if direction == "upstream":
            key = (edge.source_id, edge.target_id)
        else:
            key = (edge.target_id, edge.source_id)
        pairs[key] = edge
    return pairs


def dataset_from_location(location: str | None) -> str:
    if not location:
        return ""
    return location.rsplit("/", 1)[-1].split(".", 1)[0]
