"""Source adapter registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from clearmetric.core import CatalogArtifact
from clearmetric.core.errors import AdapterError
from clearmetric.core.experimental import require_experimental_source
from clearmetric.core.project import ClearMetricProject
from clearmetric.lineage import load_project
from clearmetric.lineage.schema import ResolverTypeOverlay, _TypedDataset

from .dbt import ingest_dbt
from .intent import ingest_intent
from .sql import ingest_sql
from .warehouse import ingest_warehouse, resolver_schema_from_warehouse

SOURCE_ORDER = ("warehouse", "dbt", "sql", "intent")

_ADAPTERS: dict[str, Callable[[ClearMetricProject], CatalogArtifact]] = {
    "warehouse": ingest_warehouse,
    "dbt": ingest_dbt,
    "sql": ingest_sql,
    "intent": ingest_intent,
}


def configured_sources(project: ClearMetricProject) -> list[str]:
    """Return configured source kinds from project config (scan/discover)."""
    enabled: list[str] = []
    if project.sources.warehouse is not None:
        enabled.append("warehouse")
    if project.sources.dbt is not None and project.sources.dbt.manifest:
        enabled.append("dbt")
    if project.sources.sql is not None and project.sources.sql.paths:
        enabled.append("sql")
    if project.sources.intent is not None and project.sources.intent.paths:
        enabled.append("intent")
    return [kind for kind in SOURCE_ORDER if kind in enabled]


def enabled_sources(project: ClearMetricProject) -> list[str]:
    configured = configured_sources(project)
    for kind in configured:
        require_experimental_source(kind)
    return configured


def _warehouse_type_overlay(project: ClearMetricProject) -> ResolverTypeOverlay | None:
    warehouse = project.sources.warehouse
    dbt = project.sources.dbt
    if warehouse is None or dbt is None or not dbt.manifest:
        return None
    skeleton = load_project(dbt.manifest, dialect=project.dialect)
    overlay, _warnings = resolver_schema_from_warehouse(
        cast(Mapping[str, _TypedDataset], skeleton.datasets),
        Path(warehouse.path),
        dialect=project.dialect,
    )
    return overlay


def ingest_source(kind: str, project: ClearMetricProject) -> CatalogArtifact:
    require_experimental_source(kind)
    if kind == "dbt":
        return ingest_dbt(project, warehouse_overlay=_warehouse_type_overlay(project))
    adapter = _ADAPTERS.get(kind)
    if adapter is None:
        raise AdapterError(f"unknown source kind: {kind}")
    return adapter(project)


def ingest_all(project: ClearMetricProject) -> list[tuple[str, CatalogArtifact]]:
    artifacts: list[tuple[str, CatalogArtifact]] = []
    for kind in enabled_sources(project):
        artifacts.append((kind, ingest_source(kind, project)))
    if not artifacts:
        raise AdapterError("no configured sources to ingest")
    return artifacts
