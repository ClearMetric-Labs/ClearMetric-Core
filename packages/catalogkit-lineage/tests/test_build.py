from __future__ import annotations

from pathlib import Path

from catalogkit.lineage import (
    build_catalog_artifact,
    build_lineage_map,
    build_openlineage_export,
    trace_downstream,
    trace_upstream,
)


def _example_root() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "projects" / "jaffle_shop"


def _folder_example_root() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "projects" / "sql_folder"


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
    manifest_path = _example_root() / "manifest.json"

    payload = build_openlineage_export(manifest_path, dialect="postgres")

    assert payload["job"]["name"] == "jaffle_shop"
    assert any(entry["name"] == "customers" for entry in payload["datasets"])
    assert any(
        entry["dataset"] == "customers" and entry["column"] == "customer_lifetime_value"
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

    payload = build_openlineage_export(tmp_path, dialect="postgres")

    grouped_entries = [
        entry
        for entry in payload["columnLineage"]
        if entry["dataset"] == "report" and entry["column"] == "total_amount"
    ]

    assert len(grouped_entries) == 1
    assert grouped_entries[0]["inputFields"] == [
        {"namespace": "catalogkit", "name": "source_a", "field": "amount"},
        {"namespace": "catalogkit", "name": "source_b", "field": "amount"},
    ]


def test_jaffle_case_amount_columns_exclude_payment_method_predicate():
    manifest_path = _example_root() / "manifest.json"

    credit_card_upstream = trace_upstream(
        manifest_path,
        dialect="postgres",
        selection="orders.credit_card_amount",
    )
    amount_upstream = trace_upstream(
        manifest_path,
        dialect="postgres",
        selection="orders.amount",
    )
    payment_method_downstream = trace_downstream(
        manifest_path,
        dialect="postgres",
        selection="raw_payments.payment_method",
    )

    assert "column:raw_payments.amount" in credit_card_upstream.related_ids
    assert not any(
        "payment_method" in related_id
        for related_id in credit_card_upstream.related_ids
    )
    assert "column:raw_payments.amount" in amount_upstream.related_ids
    assert payment_method_downstream.related_ids == [
        "column:stg_payments.payment_method"
    ]


def test_openlineage_export_accepts_prebuilt_artifact():
    manifest_path = _example_root() / "manifest.json"
    artifact = build_catalog_artifact(manifest_path, dialect="postgres")

    payload = build_openlineage_export(artifact)

    path_payload = build_openlineage_export(manifest_path, dialect="postgres")
    assert payload["datasets"] == path_payload["datasets"]
    assert payload["columnLineage"] == path_payload["columnLineage"]
    assert payload["job"]["name"] == "catalogkit"
