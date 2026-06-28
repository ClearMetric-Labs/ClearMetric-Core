"""Shared artifact models for ClearMetric Core."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]
DerivationStatus = Literal["complete", "partial", "failed", "skipped"]
DerivationSource = Literal[
    "sqlglot",
    "dbt_manifest",
    "information_schema",
    "query_logs",
]
NodeKind = Literal[
    "table",
    "cte",
    "column",
    "model",
    "report",
    "asset",
    "visual",
    "page",
    "measure",
    "metric",
    "query",
]
EdgeKind = Literal["depends_on", "feeds", "derives_from", "references", "joins"]
MatchStatus = Literal["resolved", "ambiguous", "unresolved"]


class Evidence(BaseModel):
    file: str | None = None
    location: str | None = None
    expression: str | None = None
    confidence: Confidence = "medium"


class Warning(BaseModel):
    code: str
    message: str
    location: str | None = None
    subject_id: str | None = None


class DerivationState(BaseModel):
    status: DerivationStatus
    confidence: Confidence
    source: str
    errors: list[str] = Field(default_factory=list)


class PhysicalBinding(BaseModel):
    warehouse: str
    database: str | None = None
    schema_name: str | None = Field(
        default=None,
        alias="schema",
        serialization_alias="schema",
    )
    table: str | None = None
    column: str | None = None


class Node(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: NodeKind
    name: str
    qualified_name: str | None = None
    schema_name: str | None = Field(
        default=None,
        alias="schema",
        serialization_alias="schema",
    )
    evidence: list[Evidence] = Field(default_factory=list)
    derivation: DerivationState | None = None
    bindings: list[PhysicalBinding] | None = None
    aspects: dict[str, Any] | None = None


class Edge(BaseModel):
    kind: EdgeKind
    source_id: str
    target_id: str
    label: str | None = None
    confidence: Confidence = "high"
    match_status: MatchStatus | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    derivation: DerivationState | None = None


class CatalogArtifact(BaseModel):
    version: str = "1"
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
