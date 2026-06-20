"""Artifact assembly for catalogkit-query."""

from __future__ import annotations

from typing import Any, cast

from catalogkit.core import CatalogArtifact, Edge, Evidence, Warning, merge
from sqlglot import exp

from .ctes import extract_dependency_edges
from .errors import QueryMapContractError
from .models import (
    EdgeKind as QueryMapEdgeKind,
    QueryMap,
    QuerySummary,
    RelationEdge,
    RelationUsage,
    WarningCode,
    WarningEntry,
)
from .parser import ParsedStatement
from .relations import RelationExtraction, extract_relations

_QUERYMAP_EDGE_KINDS = {"depends_on", "joins"}
_QUERYMAP_WARNING_CODES = {
    "parse_recovered",
    "select_star",
    "table_star",
    "ambiguous_output_source",
    "unresolved_output_source",
    "non_equi_join",
    "unsupported_construct",
}


def build_query_map_from_parsed(parsed: ParsedStatement) -> QueryMap:
    """Build the public catalogkit-query artifact for one parsed SQL statement."""
    artifact, relation_extraction = _build_catalog_artifact_from_parsed(parsed)

    edges = [_relation_edge_from_catalog_edge(edge) for edge in artifact.edges]
    warnings = [_warning_entry_from_warning(warning) for warning in artifact.warnings]
    summary = QuerySummary(
        dialect=parsed.dialect,
        statement_type=parsed.statement.key.lower(),
        has_ctes=bool(relation_extraction.cte_nodes),
        relation_count=len(relation_extraction.relations),
        cte_count=len(relation_extraction.cte_nodes),
        output_count=0,
    )

    return QueryMap(
        version=artifact.version,
        summary=summary,
        relations=relation_extraction.relations,
        relation_usages=[
            RelationUsage(
                relation_id=usage.relation_id,
                alias=usage.alias,
                context=usage.context,
                sql=usage.sql,
                normalized_sql=usage.sql,
            )
            for usage in relation_extraction.relation_usages
        ],
        edges=_dedupe_query_map_edges(edges),
        outputs=[],
        warnings=_dedupe_warnings(warnings),
    )


def build_catalog_artifact_from_parsed(parsed: ParsedStatement) -> CatalogArtifact:
    """Build the shared catalog artifact used for CatalogKit composition."""
    artifact, _ = _build_catalog_artifact_from_parsed(parsed)
    return artifact


def _build_catalog_artifact_from_parsed(
    parsed: ParsedStatement,
) -> tuple[CatalogArtifact, RelationExtraction]:
    """Build the internal shared artifact and keep extraction metadata nearby."""
    relation_extraction = extract_relations(parsed.root_expression, dialect=parsed.dialect)
    dependency_extraction = extract_dependency_edges(
        parsed.root_expression,
        relation_extraction=relation_extraction,
        dialect=parsed.dialect,
    )

    if not relation_extraction.nodes:
        raise QueryMapContractError("No tables or CTE relations were found in the SQL statement.")

    nodes_by_id = {node.id: node.model_copy(deep=True) for node in relation_extraction.nodes}
    for usage in relation_extraction.relation_usages:
        node = nodes_by_id[usage.relation_id]
        node.evidence.append(
            Evidence(
                location=usage.context,
                expression=usage.sql,
                confidence="high",
            )
        )

    artifact = CatalogArtifact(
        nodes=sorted(nodes_by_id.values(), key=lambda node: node.id),
        edges=dependency_extraction.edges,
        warnings=[
            *dependency_extraction.warnings,
            *_extract_contract_warnings(parsed.root_expression, dialect=parsed.dialect),
        ],
    )
    return merge(artifact), relation_extraction


def _extract_contract_warnings(
    root_expression: Any,
    *,
    dialect: str,
) -> list[Warning]:
    warnings: list[Warning] = []

    union_expression = root_expression.find(exp.Union)
    if union_expression is not None:
        warnings.append(
            Warning(
                code="unsupported_construct",
                message="UNION queries are mapped at the relation level only in the MVP.",
                location=union_expression.sql(dialect=dialect),
            )
        )
    intersect_expression = root_expression.find(exp.Intersect)
    if intersect_expression is not None:
        warnings.append(
            Warning(
                code="unsupported_construct",
                message="INTERSECT queries are mapped at the relation level only in the MVP.",
                location=intersect_expression.sql(dialect=dialect),
            )
        )
    except_expression = root_expression.find(exp.Except)
    if except_expression is not None:
        warnings.append(
            Warning(
                code="unsupported_construct",
                message="EXCEPT queries are mapped at the relation level only in the MVP.",
                location=except_expression.sql(dialect=dialect),
            )
        )

    for select in root_expression.find_all(exp.Select):
        for selection in select.expressions:
            if isinstance(selection, exp.Star):
                warnings.append(
                    Warning(
                        code="select_star",
                        message="SELECT * was detected; output mapping is deferred in the MVP.",
                        location=selection.sql(dialect=dialect),
                    )
                )
            elif isinstance(selection, exp.Column) and str(selection.name or "").strip() == "*":
                warnings.append(
                    Warning(
                        code="table_star",
                        message="table.* was detected; output mapping is deferred in the MVP.",
                        location=selection.sql(dialect=dialect),
                    )
                )

    for join in root_expression.find_all(exp.Join):
        on_clause = join.args.get("on")
        using_clause = join.args.get("using")
        if on_clause is None and using_clause is None:
            warnings.append(
                Warning(
                    code="unsupported_construct",
                    message="JOIN without ON/USING is not modeled beyond relation dependency mapping.",
                    location=join.sql(dialect=dialect),
                )
            )
            continue
        if on_clause is not None and not _is_equality_join(on_clause):
            warnings.append(
                Warning(
                    code="non_equi_join",
                    message="Non-equality join detected; MVP preserves relation dependencies but does not model join semantics.",
                    location=join.sql(dialect=dialect),
                )
            )

    return warnings


def _is_equality_join(expression: Any) -> bool:
    if isinstance(expression, exp.EQ):
        return True
    if isinstance(expression, exp.And):
        return _is_equality_join(expression.left) and _is_equality_join(expression.right)
    return False


def _relation_edge_from_catalog_edge(edge: Edge) -> RelationEdge:
    if edge.kind not in _QUERYMAP_EDGE_KINDS:
        raise QueryMapContractError(
            f"catalogkit-query cannot emit unsupported edge kind {edge.kind!r} in its public contract."
        )
    sql = edge.evidence[0].expression if edge.evidence else None
    return RelationEdge(
        kind=cast(QueryMapEdgeKind, edge.kind),
        source_id=edge.source_id,
        target_id=edge.target_id,
        label=edge.label,
        confidence=edge.confidence,
        sql=sql,
        normalized_sql=sql,
    )


def _warning_entry_from_warning(warning: Warning) -> WarningEntry:
    if warning.code not in _QUERYMAP_WARNING_CODES:
        raise QueryMapContractError(
            f"catalogkit-query cannot emit unsupported warning code {warning.code!r} in its public contract."
        )
    return WarningEntry(
        code=cast(WarningCode, warning.code),
        message=warning.message,
        location=warning.location,
    )


def _dedupe_query_map_edges(edges: list[RelationEdge]) -> list[RelationEdge]:
    seen: set[tuple[str, str, str | None]] = set()
    deduped: list[RelationEdge] = []
    for edge in edges:
        key = (edge.source_id, edge.target_id, edge.sql)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)
    return deduped


def _dedupe_warnings(warnings: list[WarningEntry]) -> list[WarningEntry]:
    seen: set[tuple[str, str, str | None]] = set()
    deduped: list[WarningEntry] = []
    for warning in warnings:
        key = (warning.code, warning.message, warning.location)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(warning)
    return deduped
