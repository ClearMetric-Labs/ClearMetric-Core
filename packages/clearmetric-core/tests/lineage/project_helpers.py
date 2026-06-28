"""Shared helpers for lineage tests."""

from __future__ import annotations

from pathlib import Path

from clearmetric.core import CatalogArtifact
from clearmetric.lineage import (
    ProjectInput,
    build_catalog_artifact_from_project,
    build_lineage_map_from_project,
    load_project,
    trace_downstream_from_artifact,
    trace_upstream_from_artifact,
)


def load_built(
    path: str | Path, *, dialect: str
) -> tuple[ProjectInput, CatalogArtifact]:
    project = load_project(path, dialect=dialect)
    artifact = build_catalog_artifact_from_project(project, dialect=dialect)
    return project, artifact


def build_lineage_map(path: str | Path, *, dialect: str):
    project = load_project(path, dialect=dialect)
    return build_lineage_map_from_project(project, dialect=dialect)


def build_catalog_artifact(path: str | Path, *, dialect: str) -> CatalogArtifact:
    _project, artifact = load_built(path, dialect=dialect)
    return artifact


def trace_upstream(path: str | Path, *, dialect: str, selection: str):
    _project, artifact = load_built(path, dialect=dialect)
    return trace_upstream_from_artifact(artifact, selection=selection)


def trace_downstream(path: str | Path, *, dialect: str, selection: str):
    _project, artifact = load_built(path, dialect=dialect)
    return trace_downstream_from_artifact(artifact, selection=selection)
