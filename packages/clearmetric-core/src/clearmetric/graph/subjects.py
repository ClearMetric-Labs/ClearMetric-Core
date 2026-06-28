"""Lineage subject helpers used during traversal and OpenLineage export."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from clearmetric.core import split_qualified_identifier
from clearmetric.core.models import CatalogArtifact, Edge, Warning

from .view import GraphView


def derives_from_edges(view: GraphView) -> list[Edge]:
    return view.edges(kind="derives_from")


def derives_from_counts_by_source_dataset(edges: Iterable[Edge]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for edge in edges:
        if edge.kind != "derives_from":
            continue
        dataset_name, _column_name = column_selection_from_id(edge.source_id)
        counts[dataset_name] += 1
    return dict(counts)


def column_selection_from_id(node_id: str) -> tuple[str, str]:
    if not node_id.startswith("column:"):
        raise ValueError(f"Expected column node id, got {node_id!r}")
    qualified_name = node_id[len("column:") :]
    parts = split_qualified_identifier(qualified_name)
    if len(parts) < 2:
        raise ValueError(f"Expected qualified column id, got {node_id!r}")
    return ".".join(parts[:-1]), parts[-1]


def dataset_from_location(location: str | None) -> str:
    if not location:
        return ""
    return location.rsplit("/", 1)[-1].split(".", 1)[0]


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


def impact_edge_kind(selection_id: str) -> str:
    if selection_id.startswith("column:"):
        return "derives_from"
    if selection_id.startswith(("metric:", "query:")):
        return "depends_on"
    raise ValueError(f"Unsupported impact selection id: {selection_id!r}")


def impact_dataset_name(selection_id: str) -> str | None:
    if not selection_id.startswith("column:"):
        return None
    return selection_id.removeprefix("column:").rsplit(".", 1)[0]
