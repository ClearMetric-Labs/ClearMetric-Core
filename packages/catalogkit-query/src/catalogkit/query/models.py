"""Public artifact models for catalogkit-query."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]
RelationKind = Literal["table", "cte"]
RelationUsageContext = Literal["from", "join", "cte_body"]
EdgeKind = Literal["depends_on", "joins"]
WarningCode = Literal[
    "parse_recovered",
    "select_star",
    "table_star",
    "ambiguous_output_source",
    "unresolved_output_source",
    "non_equi_join",
    "unsupported_construct",
]


class QuerySummary(BaseModel):
    dialect: str
    statement_type: str
    has_ctes: bool
    relation_count: int
    cte_count: int
    output_count: int = 0


class Relation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: RelationKind
    name: str
    qualified_name: str | None = None
    schema_name: str | None = Field(
        default=None,
        alias="schema",
        serialization_alias="schema",
    )


class RelationUsage(BaseModel):
    relation_id: str
    alias: str | None = None
    context: RelationUsageContext
    sql: str
    normalized_sql: str | None = None


class RelationEdge(BaseModel):
    kind: EdgeKind
    source_id: str
    target_id: str
    label: str | None = None
    confidence: Confidence = "high"
    sql: str | None = None
    normalized_sql: str | None = None


class OutputSourceHint(BaseModel):
    relation_id: str | None = None
    column_name: str | None = None
    confidence: Confidence = "medium"


class OutputColumn(BaseModel):
    name: str
    ordinal: int
    expression_sql: str
    normalized_expression_sql: str | None = None
    inferred: bool = False
    sources: list[OutputSourceHint] = Field(default_factory=list)


class WarningEntry(BaseModel):
    code: WarningCode
    message: str
    location: str | None = None


class QueryMap(BaseModel):
    version: str = "1"
    summary: QuerySummary
    relations: list[Relation] = Field(default_factory=list)
    relation_usages: list[RelationUsage] = Field(default_factory=list)
    edges: list[RelationEdge] = Field(default_factory=list)
    outputs: list[OutputColumn] = Field(default_factory=list)
    warnings: list[WarningEntry] = Field(default_factory=list)
