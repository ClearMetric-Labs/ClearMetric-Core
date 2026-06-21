"""Coverage classification helpers for catalogkit-lineage."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum

from catalogkit.core import CatalogArtifact

from .graph import column_selection_from_id, dataset_from_location, upstream_adjacency
from .loaders import ProjectInput


class ColumnResolution(str, Enum):
    RESOLVED = "resolved"
    SOURCE_LEAF = "source_leaf"
    FLAGGED = "flagged"
    SILENT = "silent"


@dataclass(frozen=True)
class _ClassificationContext:
    adjacency: dict[str, list[str]]
    flagged_ids: set[str]
    select_star_datasets: set[str]


def _classification_context(artifact: CatalogArtifact) -> _ClassificationContext:
    return _ClassificationContext(
        adjacency=upstream_adjacency(artifact),
        flagged_ids={
            warning.subject_id for warning in artifact.warnings if warning.subject_id
        },
        select_star_datasets=_datasets_with_select_star_warning(artifact),
    )


def classify_column(
    column_id: str,
    artifact: CatalogArtifact,
    project: ProjectInput,
) -> ColumnResolution:
    context = _classification_context(artifact)
    return _classify_column(
        column_id,
        project=project,
        adjacency=context.adjacency,
        flagged_ids=context.flagged_ids,
        select_star_datasets=context.select_star_datasets,
    )


def find_silent_columns(
    artifact: CatalogArtifact,
    project: ProjectInput,
) -> list[str]:
    context = _classification_context(artifact)
    return sorted(
        column_id
        for column_id in _project_column_ids(artifact, project)
        if _classify_column(
            column_id,
            project=project,
            adjacency=context.adjacency,
            flagged_ids=context.flagged_ids,
            select_star_datasets=context.select_star_datasets,
        )
        == ColumnResolution.SILENT
    )


def find_bogus_source_leaves(
    artifact: CatalogArtifact,
    project: ProjectInput,
    *,
    context: _ClassificationContext | None = None,
) -> list[str]:
    ctx = context or _classification_context(artifact)
    bogus: list[str] = []
    for column_id in _project_column_ids(artifact, project):
        parent_name, _column_name = column_selection_from_id(column_id)
        dataset = project.datasets[parent_name]
        classification = _classify_column(
            column_id,
            project=project,
            adjacency=ctx.adjacency,
            flagged_ids=ctx.flagged_ids,
            select_star_datasets=ctx.select_star_datasets,
        )
        if classification != ColumnResolution.SOURCE_LEAF:
            continue
        if dataset.kind != "root":
            bogus.append(column_id)
            continue
        if ctx.adjacency.get(column_id):
            bogus.append(column_id)
    return sorted(bogus)


def coverage_summary(
    artifact: CatalogArtifact,
    project: ProjectInput,
) -> dict[str, object]:
    context = _classification_context(artifact)
    counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    for column_id in _project_column_ids(artifact, project):
        counts[
            _classify_column(
                column_id,
                project=project,
                adjacency=context.adjacency,
                flagged_ids=context.flagged_ids,
                select_star_datasets=context.select_star_datasets,
            ).value
        ] += 1
    for warning in artifact.warnings:
        if warning.code:
            warning_counts[warning.code] += 1
    total = sum(counts.values())
    return {
        "total": total,
        "resolved": counts[ColumnResolution.RESOLVED.value],
        "source_leaf": counts[ColumnResolution.SOURCE_LEAF.value],
        "flagged": counts[ColumnResolution.FLAGGED.value],
        "silent": counts[ColumnResolution.SILENT.value],
        "bogus_source_leaves": len(
            find_bogus_source_leaves(artifact, project, context=context)
        ),
        "warning_counts": dict(sorted(warning_counts.items())),
        "resolved_pct": _percentage(
            counts[ColumnResolution.RESOLVED.value],
            total,
        ),
        "flagged_pct": _percentage(
            counts[ColumnResolution.FLAGGED.value],
            total,
        ),
    }


def _project_column_ids(
    artifact: CatalogArtifact,
    project: ProjectInput,
) -> list[str]:
    return sorted(
        node.id
        for node in artifact.nodes
        if node.kind == "column" and _is_project_column(node.id, project)
    )


def _is_project_column(column_id: str, project: ProjectInput) -> bool:
    parent_name, _column_name = column_selection_from_id(column_id)
    return parent_name in project.datasets


def _classify_column(
    column_id: str,
    *,
    project: ProjectInput,
    adjacency: dict[str, list[str]],
    flagged_ids: set[str],
    select_star_datasets: set[str],
) -> ColumnResolution:
    parent_name, _column_name = column_selection_from_id(column_id)
    dataset = project.datasets.get(parent_name)

    if column_id in flagged_ids:
        return ColumnResolution.FLAGGED
    if adjacency.get(column_id):
        return ColumnResolution.RESOLVED
    if (
        dataset is not None
        and dataset.kind == "local"
        and parent_name in select_star_datasets
    ):
        return ColumnResolution.FLAGGED
    if dataset is not None and dataset.kind == "root":
        return ColumnResolution.SOURCE_LEAF
    return ColumnResolution.SILENT


def _datasets_with_select_star_warning(artifact: CatalogArtifact) -> set[str]:
    return {
        dataset_from_location(warning.location)
        for warning in artifact.warnings
        if warning.code == "select_star"
        and warning.subject_id is None
        and warning.location
    }


def _percentage(count: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round((count / total) * 100, 2)
