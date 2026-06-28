"""Project discovery."""

from __future__ import annotations

from pathlib import Path

from clearmetric.adapters.registry import enabled_sources
from clearmetric.core.errors import CompilerError
from clearmetric.core.project import load_project_config

from .models import DiscoverReport, ResolvedSource


def discover(project_dir: Path) -> DiscoverReport:
    root = project_dir.expanduser().resolve()
    project = load_project_config(root)
    sources: list[ResolvedSource] = []

    for kind in enabled_sources(project):
        if kind == "warehouse":
            warehouse = project.sources.warehouse
            if warehouse is None:
                raise CompilerError(
                    "enabled warehouse source missing from project config"
                )
            sources.append(ResolvedSource(kind="warehouse", path=warehouse.path))
        elif kind == "dbt":
            dbt = project.sources.dbt
            if dbt is None or dbt.manifest is None:
                raise CompilerError("enabled dbt source missing from project config")
            sources.append(ResolvedSource(kind="dbt", path=dbt.manifest))
        elif kind == "sql":
            sql = project.sources.sql
            if sql is None:
                raise CompilerError("enabled sql source missing from project config")
            for path in sql.paths:
                sources.append(ResolvedSource(kind="sql", path=path))
        else:
            raise CompilerError(
                f"discover does not resolve enabled source kind: {kind!r}"
            )

    if project.aliases is not None:
        sources.append(ResolvedSource(kind="aliases", path=project.aliases))

    return DiscoverReport(
        config_path=str((root / "clearmetric.yaml").resolve()),
        dialect=project.dialect,
        sources=sources,
    )
