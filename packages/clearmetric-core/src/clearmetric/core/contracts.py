"""Contract aspect models for metric and query nodes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .errors import ValidationError as ArtifactValidationError
from .models import CatalogArtifact, Node


class MetricContract(BaseModel):
    formula: str
    unit: str | None = None
    description: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class QueryContract(BaseModel):
    sql: str
    parameters: dict[str, str] = Field(default_factory=dict)
    description: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    compiled_sql: str | None = None


def parse_metric_contract(aspects: dict[str, Any] | None) -> MetricContract | None:
    if not aspects:
        return None
    raw = aspects.get("metric")
    if raw is None:
        return None
    try:
        return MetricContract.model_validate(raw)
    except ValidationError as exc:
        raise ArtifactValidationError(f"Invalid metric contract aspect: {exc}") from exc


def parse_query_contract(aspects: dict[str, Any] | None) -> QueryContract | None:
    if not aspects:
        return None
    raw = aspects.get("query")
    if raw is None:
        return None
    try:
        return QueryContract.model_validate(raw)
    except ValidationError as exc:
        raise ArtifactValidationError(f"Invalid query contract aspect: {exc}") from exc


def contract_for_node(node: Node) -> MetricContract | QueryContract | None:
    if node.kind == "metric":
        return parse_metric_contract(node.aspects)
    if node.kind == "query":
        return parse_query_contract(node.aspects)
    return None


def query_execution_sql(node: Node) -> str | None:
    contract = parse_query_contract(node.aspects)
    if contract is None:
        return None
    return contract.compiled_sql or contract.sql or None


def find_query_node(artifact: CatalogArtifact, query_id: str) -> Node | None:
    node = next((item for item in artifact.nodes if item.id == query_id), None)
    if node is None or node.kind != "query":
        return None
    return node


def contract_dependency_violations(
    artifact: CatalogArtifact,
    *,
    node_ids: set[str] | None = None,
) -> list[str]:
    known_ids = node_ids or {node.id for node in artifact.nodes}
    violations: list[str] = []
    for node in artifact.nodes:
        contract = contract_for_node(node)
        if contract is None:
            continue
        for dep_id in contract.depends_on:
            if dep_id not in known_ids:
                violations.append(
                    f"{node.id} depends_on references missing node {dep_id!r}"
                )
    return violations


__all__ = [
    "MetricContract",
    "QueryContract",
    "contract_dependency_violations",
    "contract_for_node",
    "find_query_node",
    "parse_metric_contract",
    "parse_query_contract",
    "query_execution_sql",
]
