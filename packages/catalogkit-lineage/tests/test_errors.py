from __future__ import annotations

import json
from pathlib import Path

import pytest
from catalogkit.lineage import build_lineage_map, trace_upstream
from catalogkit.lineage.cli import main
from catalogkit.lineage.errors import LineageInputError

from .ground_truth import load_built_fixture


def test_invalid_file_input_fails_loudly(tmp_path: Path):
    invalid_file = tmp_path / "not_a_manifest.txt"
    invalid_file.write_text("hello", encoding="utf-8")

    with pytest.raises(LineageInputError):
        build_lineage_map(invalid_file, dialect="postgres")


def test_empty_sql_folder_fails_loudly(tmp_path: Path):
    with pytest.raises(LineageInputError):
        build_lineage_map(tmp_path, dialect="postgres")


def test_openlineage_and_traversal_flags_cannot_mix(capsys):
    package_root = Path(__file__).resolve().parents[1]
    manifest_path = (
        package_root
        / "tests"
        / "fixtures"
        / "projects"
        / "jaffle_shop"
        / "manifest.json"
    )

    exit_code = main(
        [
            "--dialect",
            "postgres",
            "--format",
            "openlineage",
            "--upstream",
            "customers.customer_lifetime_value",
            str(manifest_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "cannot be combined" in captured.err


def test_unknown_traversal_selection_fails_loudly():
    compiled_dir = (
        Path(__file__).resolve().parent / "fixtures" / "projects" / "sql_folder"
    )

    with pytest.raises(LineageInputError):
        trace_upstream(
            compiled_dir,
            dialect="postgres",
            selection="customers_report.missing_column",
        )


def test_manifest_compiled_path_cannot_escape_manifest_directory(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "metadata": {"project_name": "escape_test"},
                "nodes": {
                    "model.escape_test.example": {
                        "resource_type": "model",
                        "name": "example",
                        "compiled_path": "../outside.sql",
                        "depends_on": {"nodes": []},
                        "columns": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LineageInputError, match="escapes the manifest directory"):
        build_lineage_map(manifest_path, dialect="postgres")


def test_sql_folder_duplicate_dataset_names_fail_loudly(tmp_path: Path):
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders.base.sql").write_text("select 1 as id", encoding="utf-8")
    (tmp_path / "orders" / "base.sql").write_text("select 2 as id", encoding="utf-8")

    with pytest.raises(LineageInputError, match="duplicate dataset name"):
        build_lineage_map(tmp_path, dialect="postgres")


def test_lineage_resolution_failed_warning_is_recoverable(tmp_path: Path):
    (tmp_path / "valid.sql").write_text(
        "select amount from raw_orders", encoding="utf-8"
    )
    (tmp_path / "broken.sql").write_text("select from", encoding="utf-8")

    lineage_map = build_lineage_map(tmp_path, dialect="postgres")

    assert any(
        warning.code == "lineage_resolution_failed" for warning in lineage_map.warnings
    )


def test_unresolved_star_source_warning_is_emitted_for_untyped_select_star_fixture():
    fixture_root = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "adversarial"
        / "select_star_no_schema"
    )

    lineage_map = build_lineage_map(fixture_root, dialect="postgres")

    assert any(warning.code == "select_star" for warning in lineage_map.warnings)
    assert any(
        warning.code == "unresolved_star_source" for warning in lineage_map.warnings
    )


def test_unresolved_output_source_warning_is_emitted_for_table_star_alias_fixture():
    fixture_root = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "adversarial"
        / "table_star_alias"
        / "manifest.json"
    )

    lineage_map = build_lineage_map(fixture_root, dialect="postgres")

    assert any(warning.code == "select_star" for warning in lineage_map.warnings)
    assert not any(edge.kind == "derives_from" for edge in lineage_map.edges)


def test_unresolved_lineage_warning_is_emitted_for_flagged_shopify_column():
    manifest_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "projects"
        / "shopify"
        / "manifest.json"
    )

    _project, artifact = load_built_fixture(manifest_path, "postgres")

    assert any(
        warning.code == "unresolved_lineage"
        and warning.subject_id
        == "column:stg_shopify__discount_allocation._fivetran_synced"
        for warning in artifact.warnings
    )
