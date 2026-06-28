"""Public API for clearmetric-core."""

from __future__ import annotations

from typing import Any

from clearmetric.core import CatalogArtifact

from .build import (
    build_catalog_artifact_from_project,
    build_lineage_map_from_project,
    build_openlineage_export_from_artifact,
    trace_downstream_from_artifact,
    trace_upstream_from_artifact,
)
from .models import LineageMap, TraversalResult
from .render.json import render_json
from .render.text import render_text


def build_openlineage_export(
    artifact: CatalogArtifact,
    *,
    job_name: str = "clearmetric",
) -> dict[str, Any]:
    """Build an OpenLineage-compatible export from a pre-built artifact."""
    return build_openlineage_export_from_artifact(artifact, job_name=job_name)


__all__ = [
    "build_catalog_artifact_from_project",
    "build_lineage_map_from_project",
    "build_openlineage_export",
    "build_openlineage_export_from_artifact",
    "render_json",
    "render_text",
    "trace_downstream_from_artifact",
    "trace_upstream_from_artifact",
    "LineageMap",
    "TraversalResult",
]
