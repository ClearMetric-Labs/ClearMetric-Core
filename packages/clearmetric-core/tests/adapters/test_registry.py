from __future__ import annotations

from pathlib import Path

from clearmetric.adapters.registry import SOURCE_ORDER, enabled_sources, ingest_all
from clearmetric.core.project import load_project_config

from tests.wedge.helpers import setup_wedge_project


def test_source_order_and_enabled_sources(tmp_path: Path):
    project_dir = setup_wedge_project(tmp_path)
    project = load_project_config(project_dir)
    assert enabled_sources(project) == ["warehouse", "dbt"]
    assert SOURCE_ORDER == ("warehouse", "dbt", "sql", "intent")


def test_ingest_all_merges_warehouse_and_dbt(tmp_path: Path):
    project_dir = setup_wedge_project(tmp_path)
    project = load_project_config(project_dir)
    ingested = ingest_all(project)
    kinds = [kind for kind, _artifact in ingested]
    assert kinds == ["warehouse", "dbt"]
    warehouse_artifact = ingested[0][1]
    assert any(node.bindings for node in warehouse_artifact.nodes)
