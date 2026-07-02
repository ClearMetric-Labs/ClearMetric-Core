from __future__ import annotations

import json
from pathlib import Path

from clearmetric.lineage import build_catalog_artifact_from_project, load_project

FIXTURE_ROOT = (
    Path(__file__).resolve().parent.parent / "fixtures" / "lineage" / "adversarial"
)


def _write_manifest(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "compiled").mkdir(exist_ok=True)
    (path.parent / "compiled" / "upstream.sql").write_text(
        "select id, amount from raw_orders",
        encoding="utf-8",
    )
    (path.parent / "compiled" / "middle.sql").write_text(
        "select * from upstream",
        encoding="utf-8",
    )
    (path.parent / "compiled" / "downstream.sql").write_text(
        "select amount from middle",
        encoding="utf-8",
    )
    payload = {
        "metadata": {"project_name": "chain"},
        "nodes": {
            "seed.chain.raw_orders": {
                "resource_type": "seed",
                "name": "raw_orders",
                "columns": {
                    "id": {"name": "id", "data_type": "integer"},
                    "amount": {"name": "amount", "data_type": "numeric"},
                },
            },
            "model.chain.upstream": {
                "resource_type": "model",
                "name": "upstream",
                "compiled_path": "compiled/upstream.sql",
                "depends_on": {"nodes": ["seed.chain.raw_orders"]},
                "columns": {
                    "id": {"name": "id", "data_type": "integer"},
                    "amount": {"name": "amount", "data_type": "numeric"},
                },
            },
            "model.chain.middle": {
                "resource_type": "model",
                "name": "middle",
                "compiled_path": "compiled/middle.sql",
                "depends_on": {"nodes": ["model.chain.upstream"]},
                "columns": {
                    "id": {"name": "id", "data_type": "integer"},
                    "amount": {"name": "amount", "data_type": "numeric"},
                },
            },
            "model.chain.downstream": {
                "resource_type": "model",
                "name": "downstream",
                "compiled_path": "compiled/downstream.sql",
                "depends_on": {"nodes": ["model.chain.middle"]},
                "columns": {
                    "amount": {"name": "amount", "data_type": "numeric"},
                },
            },
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_three_model_chain_propagates_schema_for_select_star(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    project = load_project(manifest, dialect="postgres")
    artifact = build_catalog_artifact_from_project(project, dialect="postgres")
    edges = {
        (edge.source_id, edge.target_id)
        for edge in artifact.edges
        if edge.kind == "derives_from"
    }
    assert ("column:middle.amount", "column:upstream.amount") in edges
    assert ("column:downstream.amount", "column:middle.amount") in edges


def test_missing_type_emits_warning_without_schema_entry(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["nodes"]["seed.chain.raw_orders"]["columns"]["amount"].pop("data_type")
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    project = load_project(manifest, dialect="postgres")
    artifact = build_catalog_artifact_from_project(project, dialect="postgres")
    warning_codes = {warning.code for warning in artifact.warnings}
    assert "missing_column_type" in warning_codes
