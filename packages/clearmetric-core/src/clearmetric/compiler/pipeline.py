"""Compile pipeline stages."""

from __future__ import annotations

from pathlib import Path

from clearmetric.adapters.registry import enabled_sources, ingest_all
from clearmetric.core import attach_warehouse_bindings, merge
from clearmetric.core.project import load_project_aliases, load_project_config

from .compile_contracts import compile_query_contracts
from .link_metrics import link_contract_dependencies
from .models import CompiledGraph

PIPELINE_STAGES = (
    "discover",
    "ingest",
    "merge",
    "link",
    "bind",
    "compile_contracts",
)


def run_build(project_dir: Path) -> CompiledGraph:
    """Run build stages through bind and compile_contracts (no enforce)."""
    root = project_dir.expanduser().resolve()
    project = load_project_config(root)
    alias_map = load_project_aliases(project)

    ingested = ingest_all(project)
    artifacts = [artifact for _kind, artifact in ingested]
    merged = merge(*artifacts) if len(artifacts) > 1 else artifacts[0]
    merged = link_contract_dependencies(merged)

    warehouse_artifact = next(
        (artifact for kind, artifact in ingested if kind == "warehouse"),
        None,
    )
    if warehouse_artifact is not None:
        merged = attach_warehouse_bindings(
            merged=merged,
            warehouse_artifact=warehouse_artifact,
            alias_map=alias_map,
        )

    merged = compile_query_contracts(merged, dialect=project.dialect)

    return CompiledGraph(
        artifact=merged,
        project=project,
        project_dir=root,
        sources_run=enabled_sources(project),
    )
