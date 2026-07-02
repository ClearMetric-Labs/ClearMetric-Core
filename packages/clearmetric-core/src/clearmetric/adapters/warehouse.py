"""Warehouse metadata ingestion adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from clearmetric.core import (
    CatalogArtifact,
    Evidence,
    Node,
    PhysicalBinding,
    resolve_table_match,
    warehouse_table_fqn_candidates_from_name,
)
from clearmetric.core.errors import AdapterError
from clearmetric.core.ids import column_id, table_id
from clearmetric.core.models import DerivationState, Warning
from clearmetric.core.project import ClearMetricProject, WarehouseSource
from clearmetric.lineage.errors import LineageInputError
from clearmetric.lineage.schema import (
    ColumnTypeEntry,
    ResolverTypeOverlay,
    _TypedDataset,
    normalize_sqlglot_type,
)
from pydantic import BaseModel, Field, ValidationError


class WarehouseMetadataColumn(BaseModel):
    name: str
    data_type: str | None = None
    nullable: bool | None = None
    ordinal_position: int | None = None
    comment: str | None = None


class WarehouseMetadataTable(BaseModel):
    database: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    name: str
    columns: list[WarehouseMetadataColumn] = Field(default_factory=list)


class WarehouseMetadataDocument(BaseModel):
    warehouse: str = "information_schema"
    tables: list[WarehouseMetadataTable] = Field(default_factory=list)


def _load_warehouse_document(
    path: Path,
    *,
    invalid_json_message: str,
    invalid_schema_message: str,
) -> WarehouseMetadataDocument:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LineageInputError(invalid_json_message.format(path=path)) from exc
    try:
        return WarehouseMetadataDocument.model_validate(payload)
    except ValidationError as exc:
        raise LineageInputError(
            invalid_schema_message.format(path=path, exc=exc)
        ) from exc


def ingest_warehouse(project: ClearMetricProject) -> CatalogArtifact:
    source = project.sources.warehouse
    if source is None:
        raise AdapterError("warehouse source is not configured")
    return _ingest_information_schema(source, warehouse_name="information_schema")


def _ingest_information_schema(
    source: WarehouseSource,
    *,
    warehouse_name: str,
) -> CatalogArtifact:
    path = Path(source.path)
    try:
        document = _load_warehouse_document(
            path,
            invalid_json_message="Warehouse metadata is not valid JSON: {path}",
            invalid_schema_message="Warehouse metadata schema invalid: {path}: {exc}",
        )
    except LineageInputError as exc:
        raise AdapterError(str(exc)) from exc

    nodes: list[Node] = []
    for table in document.tables:
        qualified = _table_qualified_name(table)
        binding = PhysicalBinding(
            warehouse=document.warehouse or warehouse_name,
            database=table.database,
            schema=table.schema_name,
            table=table.name,
        )
        table_node = Node(
            id=table_id(qualified),
            kind="table",
            name=table.name,
            qualified_name=qualified,
            schema=table.schema_name,
            evidence=[
                Evidence(
                    file=str(path),
                    expression=qualified,
                    confidence="high",
                )
            ],
            derivation=DerivationState(
                status="complete",
                confidence="high",
                source="information_schema",
            ),
            bindings=[binding],
            aspects={
                "warehouse_metadata": {
                    "database": table.database,
                    "schema": table.schema_name,
                }
            },
        )
        nodes.append(table_node)
        for column in table.columns:
            column_binding = PhysicalBinding(
                warehouse=document.warehouse or warehouse_name,
                database=table.database,
                schema=table.schema_name,
                table=table.name,
                column=column.name,
            )
            nodes.append(
                Node(
                    id=column_id(qualified, column.name),
                    kind="column",
                    name=column.name,
                    qualified_name=f"{qualified}.{column.name}",
                    schema=table.schema_name,
                    evidence=[
                        Evidence(
                            file=str(path),
                            expression=f"{qualified}.{column.name}",
                            confidence="high",
                        )
                    ],
                    derivation=DerivationState(
                        status="complete",
                        confidence="high",
                        source="information_schema",
                    ),
                    bindings=[column_binding],
                    aspects={
                        "warehouse_metadata": {
                            "data_type": column.data_type,
                            "nullable": column.nullable,
                            "ordinal_position": column.ordinal_position,
                            "comment": column.comment,
                        }
                    },
                )
            )

    return CatalogArtifact(nodes=nodes, edges=[], warnings=[])


def _table_qualified_name(table: WarehouseMetadataTable) -> str:
    parts = [part for part in (table.database, table.schema_name, table.name) if part]
    if not parts:
        raise AdapterError("warehouse table missing name")
    return ".".join(parts)


def resolver_schema_from_warehouse(
    datasets: Mapping[str, _TypedDataset],
    warehouse_path: Path,
    *,
    dialect: str,
    strict: bool = False,
) -> tuple[ResolverTypeOverlay, list[Warning]]:
    """Build typed overlay from warehouse information_schema JSON."""
    warnings: list[Warning] = []
    if not warehouse_path.is_file():
        return ResolverTypeOverlay(), warnings

    document = _load_warehouse_document(
        warehouse_path,
        invalid_json_message="Warehouse metadata is not valid JSON: {path}",
        invalid_schema_message="Warehouse metadata schema invalid: {path}: {exc}",
    )

    root_datasets = {name: ds for name, ds in datasets.items() if ds.kind == "root"}
    root_table_ids = {table_id(name) for name in root_datasets}
    overlay: dict[str, dict[str, ColumnTypeEntry]] = {}

    for table in document.tables:
        qualified = _table_qualified_name(table)
        candidates = warehouse_table_fqn_candidates_from_name(qualified)
        matched_id, status = resolve_table_match(candidates, root_table_ids)
        if status == "ambiguous":
            warnings.append(
                Warning(
                    code="warehouse_bind_ambiguous",
                    message=(
                        f"Warehouse table {qualified!r} matched multiple root datasets."
                    ),
                )
            )
            continue
        if status != "resolved" or matched_id is None:
            warnings.append(
                Warning(
                    code="warehouse_bind_unresolved",
                    message=(
                        f"Warehouse table {qualified!r} could not be bound to a "
                        f"root dataset (match_status={status})."
                    ),
                )
            )
            continue
        dataset_name = matched_id.removeprefix("table:")
        column_entries: dict[str, ColumnTypeEntry] = {}
        for column in table.columns:
            normalized = normalize_sqlglot_type(
                dialect, column.data_type, strict=strict
            )
            if normalized is None:
                if column.data_type and str(column.data_type).strip():
                    warnings.append(
                        Warning(
                            code="unsupported_column_type",
                            message=(
                                f"Unsupported warehouse type {column.data_type!r} "
                                f"for {dataset_name}.{column.name}."
                            ),
                            subject_id=f"column:{dataset_name}.{column.name}",
                        )
                    )
                continue
            column_entries[column.name] = ColumnTypeEntry(
                sqlglot_type=normalized,
                source="warehouse",
            )
        if column_entries:
            overlay[dataset_name] = column_entries

    return ResolverTypeOverlay(by_dataset=overlay), warnings
