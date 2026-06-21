"""Public API for catalogkit-lineage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from catalogkit.core import CatalogArtifact

from .build import (
    build_catalog_artifact_from_project,
    build_lineage_map_from_project,
    build_openlineage_export_from_artifact,
    build_openlineage_export_from_project,
    trace_downstream_from_project,
    trace_upstream_from_project,
)
from .loaders import load_project
from .models import LineageMap, TraversalResult
from .render.json import render_json
from .render.text import render_text


def build_lineage_map(path: str | Path, *, dialect: str) -> LineageMap:
    """Build the public catalogkit-lineage artifact for one project input."""
    project = load_project(path, dialect=dialect)
    return build_lineage_map_from_project(project, dialect=dialect)


def build_catalog_artifact(path: str | Path, *, dialect: str) -> CatalogArtifact:
    """Build the shared catalog artifact for CatalogKit composition."""
    project = load_project(path, dialect=dialect)
    return build_catalog_artifact_from_project(project, dialect=dialect)


def trace_upstream(
    path: str | Path,
    *,
    dialect: str,
    selection: str,
) -> TraversalResult:
    """Trace upstream column lineage for one selected dataset column."""
    project = load_project(path, dialect=dialect)
    return trace_upstream_from_project(project, dialect=dialect, selection=selection)


def trace_downstream(
    path: str | Path,
    *,
    dialect: str,
    selection: str,
) -> TraversalResult:
    """Trace downstream column lineage for one selected dataset column."""
    project = load_project(path, dialect=dialect)
    return trace_downstream_from_project(project, dialect=dialect, selection=selection)


def build_openlineage_export(
    source: str | Path | CatalogArtifact,
    *,
    dialect: str = "",
    job_name: str = "catalogkit",
) -> dict[str, Any]:
    """Build an OpenLineage-compatible export from a path or a pre-built artifact.

    When ``source`` is a ``CatalogArtifact``, ``job_name`` labels the export job.
    When ``source`` is a path, ``dialect`` is required and the job name comes
    from the loaded project.
    """
    if isinstance(source, CatalogArtifact):
        return build_openlineage_export_from_artifact(source, job_name=job_name)
    if not dialect:
        raise TypeError("dialect is required when source is a file path")
    project = load_project(source, dialect=dialect)
    return build_openlineage_export_from_project(project, dialect=dialect)


__all__ = [
    "build_catalog_artifact",
    "build_lineage_map",
    "build_openlineage_export",
    "render_json",
    "render_text",
    "trace_downstream",
    "trace_upstream",
]
