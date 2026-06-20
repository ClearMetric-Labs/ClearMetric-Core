"""Shared artifact models for CatalogKit."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]
NodeKind = Literal["table", "cte", "column", "model", "report", "asset"]
EdgeKind = Literal["depends_on", "feeds", "derives_from", "references", "joins"]


class Evidence(BaseModel):
    file: str | None = None
    location: str | None = None
    expression: str | None = None
    confidence: Confidence = "medium"


class Warning(BaseModel):
    code: str
    message: str
    location: str | None = None


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


class Edge(BaseModel):
    kind: EdgeKind
    source_id: str
    target_id: str
    label: str | None = None
    confidence: Confidence = "high"
    evidence: list[Evidence] = Field(default_factory=list)


class CatalogArtifact(BaseModel):
    version: str = "1"
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
