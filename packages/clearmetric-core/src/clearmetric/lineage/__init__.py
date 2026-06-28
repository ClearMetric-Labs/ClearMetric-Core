"""Public package surface for clearmetric-core."""

from __future__ import annotations

from clearmetric.core import CatalogArtifact

from ._version import __version__
from .api import (
    build_catalog_artifact_from_project,
    build_lineage_map_from_project,
    build_openlineage_export,
    build_openlineage_export_from_artifact,
    render_json,
    render_text,
    trace_downstream_from_artifact,
    trace_upstream_from_artifact,
)
from .errors import LineageContractError, LineageError, LineageInputError
from .loaders import ProjectInput, load_project
from .models import LineageMap, LineageSummary, TraversalResult

__all__ = [
    "__version__",
    "build_catalog_artifact_from_project",
    "build_lineage_map_from_project",
    "build_openlineage_export",
    "build_openlineage_export_from_artifact",
    "CatalogArtifact",
    "LineageContractError",
    "LineageError",
    "LineageInputError",
    "LineageMap",
    "LineageSummary",
    "ProjectInput",
    "load_project",
    "render_json",
    "render_text",
    "trace_downstream_from_artifact",
    "trace_upstream_from_artifact",
    "TraversalResult",
]
