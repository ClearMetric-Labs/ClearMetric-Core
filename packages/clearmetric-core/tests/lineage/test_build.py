from __future__ import annotations

from pathlib import Path

import pytest

from clearmetric.emitters.openlineage import build_openlineage_payload
from clearmetric.lineage import load_project
from clearmetric.lineage.errors import LineageContractError
from clearmetric.lineage.build import (
    build_catalog_artifact_from_project,
    build_scoped_lineage_cached,
    edges_by_model_from_artifact,
    edges_by_model_from_project,
)
from clearmetric.lineage.loaders import (
    ProjectDataset,
    ProjectInput,
    subset_project,
    union_build_scope_for_targets,
)

from .project_helpers import (
    build_catalog_artifact,
    build_lineage_map,
    trace_downstream,
)


def _example_root() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "lineage"
        / "projects"
        / "jaffle_shop"
    )


def _folder_example_root() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "lineage"
        / "projects"
        / "sql_folder"
    )


def test_build_lineage_map_from_manifest():
    manifest_path = _example_root() / "manifest.json"

    lineage_map = build_lineage_map(manifest_path, dialect="postgres")

    assert lineage_map.summary.input_kind == "dbt_manifest"
    assert lineage_map.summary.dataset_count >= 8
    assert lineage_map.summary.column_count >= 20


def test_folder_input_builds_successfully():
    compiled_dir = _folder_example_root()

    lineage_map = build_lineage_map(compiled_dir, dialect="postgres")

    assert lineage_map.summary.input_kind == "sql_folder"
    assert lineage_map.warnings == []


def test_openlineage_export_contains_column_lineage_entries():
    compiled_dir = _folder_example_root()
    artifact = build_catalog_artifact(compiled_dir, dialect="postgres")

    payload = build_openlineage_payload(artifact, job_name="sql_folder")

    assert payload["job"]["name"] == "sql_folder"
    assert any(entry["name"] == "orders_base" for entry in payload["datasets"])
    assert any(
        entry["dataset"] == "orders_base" and entry["column"] == "amount"
        for entry in payload["columnLineage"]
    )


def test_openlineage_export_groups_multiple_inputs_per_output_column(tmp_path: Path):
    report_sql = tmp_path / "report.sql"
    report_sql.write_text(
        """
        select
            source_a.amount + source_b.amount as total_amount
        from source_a
        join source_b
            on source_a.id = source_b.id
        """.strip(),
        encoding="utf-8",
    )

    payload = build_openlineage_payload(
        build_catalog_artifact(report_sql.parent, dialect="postgres"),
        job_name=report_sql.parent.name,
    )

    grouped_entries = [
        entry
        for entry in payload["columnLineage"]
        if entry["dataset"] == "report" and entry["column"] == "total_amount"
    ]

    assert len(grouped_entries) == 1
    assert grouped_entries[0]["inputFields"] == [
        {"namespace": "clearmetric", "name": "source_a", "field": "amount"},
        {"namespace": "clearmetric", "name": "source_b", "field": "amount"},
    ]


def _star_project(sql: str) -> ProjectInput:
    return ProjectInput(
        input_kind="dbt_manifest",
        label="star",
        datasets={
            "db.raw.people": ProjectDataset(
                name="db.raw.people",
                kind="root",
                sql=None,
                dependency_names=(),
                declared_columns=("id", "name"),
                evidence_file=None,
            ),
            "db.core.patient": ProjectDataset(
                name="db.core.patient",
                kind="local",
                sql=sql,
                dependency_names=("db.raw.people",),
                declared_columns=("id", "name"),
                evidence_file=None,
            ),
        },
    )


def test_registry_proven_single_relation_star_emits_names_only_edges() -> None:
    result = edges_by_model_from_project(
        _star_project("select * from db.raw.people"),
        dialect="duckdb",
    )["db.core.patient"]

    assert result.edges == frozenset(
        {
            ("db.raw.people", "id", "db.core.patient", "id"),
            ("db.raw.people", "name", "db.core.patient", "name"),
        }
    )


def test_alias_qualified_star_remains_suppressed_for_existing_adversarial_contract() -> None:
    result = edges_by_model_from_project(
        _star_project("select p.* from db.raw.people as p"),
        dialect="duckdb",
    )["db.core.patient"]

    assert result.edges == frozenset()


def test_ambiguous_multi_relation_star_emits_no_edges() -> None:
    project = _star_project(
        "select * from db.raw.people join db.raw.people as other on people.id = other.id"
    )
    result = edges_by_model_from_project(project, dialect="duckdb")["db.core.patient"]

    assert result.edges == frozenset()


def test_jaffle_star_models_suppress_column_lineage():
    manifest_path = _example_root() / "manifest.json"

    lineage_map = build_lineage_map(manifest_path, dialect="postgres")

    assert not any(edge.kind == "derives_from" for edge in lineage_map.edges)
    assert any(warning.code == "select_star" for warning in lineage_map.warnings)

    payment_method_downstream = trace_downstream(
        manifest_path,
        dialect="postgres",
        selection="raw_payments.payment_method",
    )

    assert payment_method_downstream.related_ids == []


def test_openlineage_export_accepts_prebuilt_artifact():
    manifest_path = _example_root() / "manifest.json"
    artifact = build_catalog_artifact(manifest_path, dialect="postgres")

    payload = build_openlineage_payload(artifact)

    assert payload["datasets"]
    assert payload["job"]["name"] == "clearmetric"
    assert isinstance(payload["columnLineage"], list)


REF_CHAIN = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "lineage"
    / "seed"
    / "ref_chain"
)


def test_edges_by_model_from_artifact_matches_build() -> None:
    project = load_project(REF_CHAIN / "manifest.json", dialect="postgres")
    artifact = build_catalog_artifact_from_project(project, dialect="postgres")
    from_build = {
        model: result.edges
        for model, result in edges_by_model_from_project(
            project, dialect="postgres"
        ).items()
    }
    assert edges_by_model_from_artifact(artifact) == from_build


def test_union_build_scope_matches_individual_scopes() -> None:
    project = load_project(REF_CHAIN / "manifest.json", dialect="postgres")
    manifest = REF_CHAIN / "manifest.json"
    targets = ["downstream", "middle"]
    union_scope = union_build_scope_for_targets(
        project,
        full_manifest_path=manifest,
        slice_manifest_path=manifest,
        target_models=targets,
        dialect="postgres",
    )
    union_edges = edges_by_model_from_project(
        subset_project(project, union_scope),
        dialect="postgres",
    )
    for target in targets:
        single_scope = union_build_scope_for_targets(
            project,
            full_manifest_path=manifest,
            slice_manifest_path=manifest,
            target_models=[target],
            dialect="postgres",
        )
        single_edges = edges_by_model_from_project(
            subset_project(project, single_scope),
            dialect="postgres",
        )
        assert union_edges[target].edges == single_edges[target].edges


def test_build_scoped_lineage_cached_hit_skips_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = load_project(REF_CHAIN / "manifest.json", dialect="postgres")
    scope = frozenset(project.local_dataset_names())
    fingerprint = "test-fingerprint"
    calls = {"count": 0}
    original = build_catalog_artifact_from_project

    def counting_build(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "clearmetric.lineage.build.build_catalog_artifact_from_project",
        counting_build,
    )
    build_scoped_lineage_cached(
        project, scope, tmp_path, dialect="postgres", fingerprint=fingerprint, force=True
    )
    build_scoped_lineage_cached(
        project, scope, tmp_path, dialect="postgres", fingerprint=fingerprint, force=False
    )
    assert calls["count"] == 1


def test_stale_scoped_cache_raises_without_force(tmp_path: Path) -> None:
    import yaml

    from clearmetric.lineage.build import _scoped_cache_paths

    project = load_project(REF_CHAIN / "manifest.json", dialect="postgres")
    scope = frozenset(project.local_dataset_names())
    fingerprint = "fp-a"
    build_scoped_lineage_cached(
        project, scope, tmp_path, dialect="postgres", fingerprint=fingerprint, force=True
    )
    _cache_key, _artifact_path, meta_path = _scoped_cache_paths(
        tmp_path, fingerprint=fingerprint, scope_models=scope
    )
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    assert isinstance(meta, dict)
    meta["fingerprint"] = "corrupted"
    meta_path.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    with pytest.raises(LineageContractError, match="stale"):
        build_scoped_lineage_cached(
            project,
            scope,
            tmp_path,
            dialect="postgres",
            fingerprint=fingerprint,
            force=False,
        )
