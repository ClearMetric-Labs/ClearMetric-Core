"""Centralized column type resolution for sqlglot lineage schema."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from clearmetric.core.models import Warning

from .errors import LineageInputError


class _TypedDataset(Protocol):
    name: str
    kind: Literal["local", "root"]
    declared_columns: tuple[str, ...]
    unique_id: str | None


TypeSource = Literal["warehouse", "catalog", "manifest"]

# Explicit per-dialect mappings. Extend as real projects surface new types.
_COMMON_TYPE_MAP: dict[str, str] = {
    "boolean": "boolean",
    "bool": "boolean",
    "integer": "int",
    "int": "int",
    "int4": "int",
    "int8": "bigint",
    "bigint": "bigint",
    "smallint": "smallint",
    "float": "float",
    "float4": "float",
    "float8": "double",
    "double": "double",
    "double precision": "double",
    "real": "float",
    "numeric": "decimal",
    "decimal": "decimal",
    "number": "decimal",
    "varchar": "varchar",
    "text": "text",
    "string": "text",
    "char": "varchar",
    "character varying": "varchar",
    "date": "date",
    "timestamp": "timestamp",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamptz",
    "timestamptz": "timestamptz",
    "datetime": "timestamp",
    "time": "time",
    "json": "json",
    "jsonb": "json",
    "uuid": "uuid",
}

_DIALECT_TYPE_MAP: dict[str, dict[str, str]] = {
    "postgres": dict(_COMMON_TYPE_MAP),
    "duckdb": dict(_COMMON_TYPE_MAP),
    "snowflake": {
        **_COMMON_TYPE_MAP,
        "variant": "variant",
        "object": "object",
        "array": "array",
        "timestamp_ntz": "timestamp",
        "timestamp_ltz": "timestamptz",
        "timestamp_tz": "timestamptz",
    },
    "bigquery": {
        **_COMMON_TYPE_MAP,
        "int64": "bigint",
        "float64": "double",
        "bool": "boolean",
        "bytes": "binary",
    },
}


@dataclass(frozen=True)
class ColumnTypeEntry:
    sqlglot_type: str
    source: TypeSource


@dataclass(frozen=True)
class ResolverTypeOverlay:
    """Typed column overlay keyed by dataset identity then column name."""

    by_dataset: dict[str, dict[str, ColumnTypeEntry]] = field(default_factory=dict)


def normalize_sqlglot_type(
    dialect: str,
    raw_type: str | None,
    *,
    strict: bool = False,
) -> str | None:
    """Map a warehouse/dbt raw type string to a sqlglot-compatible type name."""
    cleaned = str(raw_type or "").strip().lower()
    if not cleaned:
        return None
    # Strip precision/scale suffixes: varchar(255), number(38,0)
    base = cleaned.split("(", 1)[0].strip()
    dialect_map = _DIALECT_TYPE_MAP.get(dialect.lower(), _COMMON_TYPE_MAP)
    mapped = dialect_map.get(base) or dialect_map.get(cleaned)
    if mapped is not None:
        return mapped
    if strict:
        raise LineageInputError(
            f"Unmapped column type {raw_type!r} for dialect {dialect!r}."
        )
    return None


def to_sqlglot_schema(
    datasets: Mapping[str, _TypedDataset],
    *,
    include_local: set[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Build sqlglot schema dict from datasets with resolved column types only."""
    schema: dict[str, dict[str, str]] = {}
    for name, dataset in datasets.items():
        if include_local is not None and name not in include_local:
            if dataset.kind != "root":
                continue
        elif dataset.kind not in {"root", "local"}:
            continue
        column_types = getattr(dataset, "column_types", {})
        if not column_types:
            continue
        schema[name] = dict(column_types)
    return schema


def resolve_dataset_column_types(
    *,
    dataset_name: str,
    declared_columns: tuple[str, ...],
    manifest_types: dict[str, str | None],
    catalog_overlay: ResolverTypeOverlay | None,
    warehouse_overlay: ResolverTypeOverlay | None,
    dialect: str,
    strict: bool = False,
) -> tuple[dict[str, str], dict[str, TypeSource], list[Warning]]:
    """Resolve column types for one dataset with precedence."""
    warnings: list[Warning] = []
    column_types: dict[str, str] = {}
    type_sources: dict[str, TypeSource] = {}
    catalog = catalog_overlay.by_dataset if catalog_overlay else {}
    warehouse = warehouse_overlay.by_dataset if warehouse_overlay else {}
    all_columns = set(declared_columns) | set(manifest_types)

    for column_name in sorted(all_columns):
        manifest_raw = manifest_types.get(column_name)
        catalog_entry = catalog.get(dataset_name, {}).get(column_name)
        warehouse_entry = warehouse.get(dataset_name, {}).get(column_name)
        catalog_raw = catalog_entry.sqlglot_type if catalog_entry else None
        warehouse_raw = warehouse_entry.sqlglot_type if warehouse_entry else None

        winner, source, conflict_warnings = merge_type_sources(
            dataset_name=dataset_name,
            column_name=column_name,
            manifest_raw=manifest_raw,
            catalog_raw=catalog_raw,
            warehouse_raw=warehouse_raw,
            dialect=dialect,
            strict=strict,
        )
        warnings.extend(conflict_warnings)
        if winner is not None and source is not None:
            column_types[column_name] = winner
            type_sources[column_name] = source

    return column_types, type_sources, warnings


def merge_type_sources(
    *,
    dataset_name: str,
    column_name: str,
    manifest_raw: str | None,
    catalog_raw: str | None,
    warehouse_raw: str | None,
    dialect: str,
    strict: bool = False,
) -> tuple[str | None, TypeSource | None, list[Warning]]:
    """Apply precedence: warehouse > catalog > manifest. Emit type_conflict warnings."""
    warnings: list[Warning] = []
    candidates: list[tuple[TypeSource, str | None, str | None]] = [
        ("warehouse", warehouse_raw, None),
        ("catalog", catalog_raw, None),
        ("manifest", manifest_raw, None),
    ]
    resolved: list[tuple[TypeSource, str]] = []
    for source, raw, _ in candidates:
        if raw is None or not str(raw).strip():
            continue
        normalized = normalize_sqlglot_type(dialect, raw, strict=strict)
        if normalized is not None:
            resolved.append((source, normalized))

    if not resolved:
        return None, None, warnings

    winner_source, winner_type = resolved[0]
    for loser_source, loser_type in resolved[1:]:
        if loser_type != winner_type:
            warnings.append(
                Warning(
                    code="type_conflict",
                    message=(
                        f"Column {dataset_name}.{column_name} has conflicting types: "
                        f"{winner_source}={winner_type!r} chosen over "
                        f"{loser_source}={loser_type!r}."
                    ),
                    subject_id=f"column:{dataset_name}.{column_name}",
                )
            )
    return winner_type, winner_source, warnings


def overlay_types_from_dbt_catalog(
    datasets: Mapping[str, _TypedDataset],
    catalog_path: Path,
    *,
    dialect: str,
    strict: bool = False,
) -> ResolverTypeOverlay:
    """Parse dbt catalog.json and produce a typed overlay."""
    if not catalog_path.is_file():
        return ResolverTypeOverlay()
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LineageInputError(
            f"dbt catalog.json is not valid JSON: {catalog_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise LineageInputError(
            f"dbt catalog.json root must be an object: {catalog_path}"
        )

    nodes = payload.get("nodes", {})
    sources = payload.get("sources", {})
    if nodes is not None and not isinstance(nodes, dict):
        raise LineageInputError("dbt catalog.json nodes must be an object")
    if sources is not None and not isinstance(sources, dict):
        raise LineageInputError("dbt catalog.json sources must be an object")

    identity_by_unique: dict[str, str] = {
        ds.unique_id: ds.name for ds in datasets.values() if ds.unique_id
    }
    overlay: dict[str, dict[str, ColumnTypeEntry]] = {}

    for section in (nodes or {}, sources or {}):
        for unique_id, node_payload in section.items():
            if not isinstance(node_payload, dict):
                raise LineageInputError(
                    f"dbt catalog entry {unique_id!r} must be an object"
                )
            identity = identity_by_unique.get(str(unique_id))
            if identity is None:
                continue
            columns = node_payload.get("columns", {})
            if not isinstance(columns, dict):
                continue
            for _col_key, col_payload in columns.items():
                if not isinstance(col_payload, dict):
                    continue
                col_name = str(col_payload.get("name") or "").strip()
                raw_type = col_payload.get("type") or col_payload.get("data_type")
                normalized = normalize_sqlglot_type(
                    dialect, str(raw_type) if raw_type else None, strict=strict
                )
                if normalized is None:
                    continue
                overlay.setdefault(identity, {})[col_name] = ColumnTypeEntry(
                    sqlglot_type=normalized,
                    source="catalog",
                )
    return ResolverTypeOverlay(by_dataset=overlay)


def overlay_from_frozen_schema(
    frozen: dict[str, dict[str, str]],
) -> ResolverTypeOverlay:
    """Build a typed overlay from a hand-frozen schema dict."""
    by_dataset: dict[str, dict[str, ColumnTypeEntry]] = {}
    for dataset_name, columns in frozen.items():
        entries = {
            column_name: ColumnTypeEntry(
                sqlglot_type=column_type,
                source="manifest",
            )
            for column_name, column_type in columns.items()
        }
        if entries:
            by_dataset[dataset_name] = entries
    return ResolverTypeOverlay(by_dataset=by_dataset)


def load_frozen_schema_overlay(path: Path) -> ResolverTypeOverlay | None:
    """Load optional per-case schema.json overlay from disk."""
    if not path.is_file():
        return None
    return resolve_type_overlay(path)


def resolve_type_overlay(path: Path, *, dialect: str = "duckdb") -> ResolverTypeOverlay:
    """Load warehouse metadata export or frozen schema dict; loud errors only."""
    if not path.is_file():
        raise LineageInputError(f"Type overlay not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LineageInputError(f"Type overlay is not valid JSON: {path}") from exc

    if isinstance(payload, dict) and "tables" in payload:
        return _overlay_from_warehouse_document(payload, path=path, dialect=dialect)

    if isinstance(payload, dict) and payload:
        if not all(isinstance(value, dict) for value in payload.values()):
            raise LineageInputError(
                f"Type overlay root must be relation→columns object or warehouse export: {path}"
            )
        frozen: dict[str, dict[str, str]] = {}
        for dataset_name, columns in payload.items():
            frozen[str(dataset_name)] = {
                str(column_name): str(column_type)
                for column_name, column_type in columns.items()
            }
        return overlay_from_frozen_schema(frozen)

    raise LineageInputError(
        f"Type overlay has unknown shape (expected warehouse export or frozen schema): {path}"
    )


def _overlay_from_warehouse_document(
    payload: dict[str, object],
    *,
    path: Path,
    dialect: str,
) -> ResolverTypeOverlay:
    tables = payload.get("tables")
    if not isinstance(tables, list):
        raise LineageInputError(f"Warehouse metadata tables must be a list: {path}")
    by_dataset: dict[str, dict[str, ColumnTypeEntry]] = {}
    for table in tables:
        if not isinstance(table, dict):
            raise LineageInputError(f"Warehouse metadata table must be an object: {path}")
        table_name = str(table.get("name") or "").strip()
        if not table_name:
            raise LineageInputError(f"Warehouse metadata table missing name: {path}")
        parts = [
            str(part).strip()
            for part in (
                table.get("database"),
                table.get("schema") or table.get("schema_name"),
                table_name,
            )
            if str(part or "").strip()
        ]
        qualified = ".".join(parts)
        columns = table.get("columns", [])
        if not isinstance(columns, list):
            raise LineageInputError(
                f"Warehouse metadata columns must be a list for {qualified}: {path}"
            )
        entries: dict[str, ColumnTypeEntry] = {}
        for column in columns:
            if not isinstance(column, dict):
                raise LineageInputError(
                    f"Warehouse metadata column must be an object for {qualified}: {path}"
                )
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                raise LineageInputError(
                    f"Warehouse metadata column missing name for {qualified}: {path}"
                )
            raw_type = column.get("data_type")
            normalized = normalize_sqlglot_type(
                dialect,
                str(raw_type) if raw_type is not None else None,
            )
            if normalized is None:
                raw = str(raw_type or "").strip()
                if not raw:
                    raise LineageInputError(
                        f"Warehouse column {qualified}.{column_name} missing data_type: {path}"
                    )
                normalized = raw
            entries[column_name] = ColumnTypeEntry(
                sqlglot_type=normalized,
                source="warehouse",
            )
        if entries:
            by_dataset[qualified] = entries
    return ResolverTypeOverlay(by_dataset=by_dataset)


def export_typed_schema_dict(project) -> dict[str, dict[str, str]]:
    """Return the typed schema dict for a loaded project."""
    return project.typed_schema()
