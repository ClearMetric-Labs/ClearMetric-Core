"""dbt manifest ingestion adapter."""

from __future__ import annotations

from clearmetric.core import CatalogArtifact
from clearmetric.core.errors import AdapterError
from clearmetric.core.project import ClearMetricProject
from clearmetric.lineage import (
    LineageError,
    build_catalog_artifact_from_project,
    load_project,
)
from clearmetric.lineage.schema import ResolverTypeOverlay


def ingest_dbt(
    project: ClearMetricProject,
    *,
    warehouse_overlay: ResolverTypeOverlay | None = None,
) -> CatalogArtifact:
    manifest = project.sources.dbt.manifest if project.sources.dbt else None
    if not manifest:
        raise AdapterError("dbt source is not configured")
    try:
        loaded = load_project(
            manifest,
            dialect=project.dialect,
            warehouse_overlay=warehouse_overlay,
        )
        return build_catalog_artifact_from_project(loaded, dialect=project.dialect)
    except LineageError as exc:
        raise AdapterError(f"dbt ingestion failed: {exc}") from exc
