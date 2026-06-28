"""Canonical graph read API for clearmetric-core."""

from __future__ import annotations

from clearmetric.core.models import CatalogArtifact

from .selector import SelectorPredicate, matches_selector, parse_selector
from .subjects import (
    column_selection_from_id,
    dataset_from_location,
    derives_from_counts_by_source_dataset,
    derives_from_edges,
    impact_dataset_name,
    impact_edge_kind,
    warnings_for_subject,
)
from .traverse import (
    TraversalDirection,
    build_traversal_subgraph,
    downstream_adjacency,
    neighbors,
    traverse,
    upstream_adjacency,
    walk_related,
)
from .view import GraphView


def view_of(artifact: CatalogArtifact) -> GraphView:
    return GraphView.from_artifact(artifact)


__all__ = [
    "GraphView",
    "SelectorPredicate",
    "TraversalDirection",
    "build_traversal_subgraph",
    "column_selection_from_id",
    "dataset_from_location",
    "derives_from_counts_by_source_dataset",
    "derives_from_edges",
    "downstream_adjacency",
    "impact_dataset_name",
    "impact_edge_kind",
    "matches_selector",
    "neighbors",
    "parse_selector",
    "traverse",
    "upstream_adjacency",
    "view_of",
    "walk_related",
    "warnings_for_subject",
]
