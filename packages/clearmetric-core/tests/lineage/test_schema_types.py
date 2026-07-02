from __future__ import annotations

from pathlib import Path

import pytest
from clearmetric.lineage.errors import LineageInputError
from clearmetric.lineage.schema import (
    merge_type_sources,
    normalize_sqlglot_type,
    overlay_types_from_dbt_catalog,
    resolve_dataset_column_types,
    resolve_type_overlay,
)

PACKAGE_SRC = Path(__file__).resolve().parents[2] / "src" / "clearmetric" / "lineage"


def test_normalize_sqlglot_type_maps_postgres_varchar() -> None:
    assert normalize_sqlglot_type("postgres", "varchar") == "varchar"
    assert normalize_sqlglot_type("postgres", "INTEGER") == "int"


def test_normalize_sqlglot_type_empty_returns_none() -> None:
    assert normalize_sqlglot_type("postgres", None) is None
    assert normalize_sqlglot_type("postgres", "") is None


def test_normalize_sqlglot_type_unmapped_emits_none_not_error_by_default() -> None:
    assert normalize_sqlglot_type("postgres", "totally_custom_type") is None


def test_normalize_sqlglot_type_strict_raises_on_unmapped() -> None:
    with pytest.raises(LineageInputError, match="Unmapped column type"):
        normalize_sqlglot_type("postgres", "totally_custom_type", strict=True)


def test_type_precedence_warehouse_wins_over_manifest() -> None:
    winner, source, warnings = merge_type_sources(
        dataset_name="raw_orders",
        column_name="amount",
        manifest_raw="varchar",
        catalog_raw="int",
        warehouse_raw="decimal",
        dialect="postgres",
    )
    assert winner == "decimal"
    assert source == "warehouse"
    assert warnings


def test_resolve_dataset_column_types_uses_manifest_when_only_source() -> None:
    types, sources, warnings = resolve_dataset_column_types(
        dataset_name="raw_orders",
        declared_columns=("id", "amount"),
        manifest_types={"id": "integer", "amount": "numeric"},
        catalog_overlay=None,
        warehouse_overlay=None,
        dialect="postgres",
    )
    assert types["id"] == "int"
    assert types["amount"] == "decimal"
    assert sources["id"] == "manifest"
    assert not warnings


def test_overlay_catalog_invalid_json_raises() -> None:
    bad_path = Path(__file__).parent / "_tmp_bad_catalog.json"
    bad_path.write_text("{not json", encoding="utf-8")
    try:
        with pytest.raises(LineageInputError, match="not valid JSON"):
            overlay_types_from_dbt_catalog({}, bad_path, dialect="postgres")
    finally:
        bad_path.unlink(missing_ok=True)


def test_resolver_schema_has_no_text_fallback_literal() -> None:
    for path in PACKAGE_SRC.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert 'column_name: "text"' not in text
        assert '"text" for column_name' not in text


def test_resolve_type_overlay_frozen_schema(tmp_path: Path) -> None:
    overlay_path = tmp_path / "overlay.json"
    overlay_path.write_text(
        '{"db.s.t": {"id": "integer", "name": "varchar"}}',
        encoding="utf-8",
    )
    overlay = resolve_type_overlay(overlay_path, dialect="duckdb")
    assert "db.s.t" in overlay.by_dataset
    assert overlay.by_dataset["db.s.t"]["id"].sqlglot_type == "integer"


def test_resolve_type_overlay_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(LineageInputError, match="not found"):
        resolve_type_overlay(tmp_path / "missing.json")


def test_resolve_type_overlay_invalid_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(LineageInputError, match="not valid JSON"):
        resolve_type_overlay(bad)


def test_resolve_type_overlay_unknown_shape_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('["not", "a", "dict"]', encoding="utf-8")
    with pytest.raises(LineageInputError, match="unknown shape"):
        resolve_type_overlay(bad)
