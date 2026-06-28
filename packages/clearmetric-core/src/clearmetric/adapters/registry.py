"""Source adapter registry."""

from __future__ import annotations

from collections.abc import Callable

from clearmetric.core import CatalogArtifact
from clearmetric.core.errors import AdapterError
from clearmetric.core.project import ClearMetricProject

from .dbt import ingest_dbt
from .intent import ingest_intent
from .sql import ingest_sql
from .warehouse import ingest_warehouse

SOURCE_ORDER = ("warehouse", "dbt", "sql", "intent")

_ADAPTERS: dict[str, Callable[[ClearMetricProject], CatalogArtifact]] = {
    "warehouse": ingest_warehouse,
    "dbt": ingest_dbt,
    "sql": ingest_sql,
    "intent": ingest_intent,
}


def enabled_sources(project: ClearMetricProject) -> list[str]:
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


def ingest_source(kind: str, project: ClearMetricProject) -> CatalogArtifact:
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
