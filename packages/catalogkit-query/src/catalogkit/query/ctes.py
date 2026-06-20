"""Dependency edge extraction for catalogkit-query."""

from __future__ import annotations

from dataclasses import dataclass

from catalogkit.core import Edge, Evidence, Warning, cte_id, normalize_identifier, normalize_identifier_part
from sqlglot import exp

from .ast_utils import cte_name, iter_ctes, iter_table_nodes, iter_table_nodes_skipping_ctes, qualified_table_name
from .errors import QueryMapContractError
from .relations import RelationExtraction


@dataclass(frozen=True)
class DependencyExtraction:
    edges: list[Edge]
    warnings: list[Warning]


def extract_dependency_edges(
    root_expression: exp.Expression,
    *,
    relation_extraction: RelationExtraction,
    dialect: str,
) -> DependencyExtraction:
    """Extract relation-level dependency edges for root query and CTEs."""
    edges: list[Edge] = []
    warnings: list[Warning] = []

    for cte in iter_ctes(root_expression):
        raw_cte_name = cte_name(cte)
        if not raw_cte_name:
            continue
        source_id = cte_id(raw_cte_name)
        for table in iter_table_nodes(cte.this):
            target_id = _resolve_relation_id(table, relation_extraction=relation_extraction)
            if target_id is None:
                continue
            if target_id == source_id:
                warnings.append(
                    Warning(
                        code="unsupported_construct",
                        message=f"Recursive CTE {normalize_identifier_part(raw_cte_name)!r} is not modeled explicitly in the MVP.",
                        location=table.sql(dialect=dialect),
                    )
                )
                continue
            edges.append(
                Edge(
                    kind="depends_on",
                    source_id=source_id,
                    target_id=target_id,
                    label="depends_on",
                    confidence="high",
                    evidence=[
                        Evidence(
                            location="cte_body",
                            expression=table.sql(dialect=dialect),
                            confidence="high",
                        )
                    ],
                )
            )

    for table in iter_table_nodes_skipping_ctes(root_expression):
        target_id = _resolve_relation_id(table, relation_extraction=relation_extraction)
        if target_id is None:
            continue
        edges.append(
            Edge(
                kind="depends_on",
                source_id="query:root",
                target_id=target_id,
                label="depends_on",
                confidence="high",
                evidence=[
                    Evidence(
                        location="root_query",
                        expression=table.sql(dialect=dialect),
                        confidence="high",
                    )
                ],
            )
        )

    return DependencyExtraction(edges=edges, warnings=warnings)


def _resolve_relation_id(
    table: exp.Table,
    *,
    relation_extraction: RelationExtraction,
) -> str | None:
    name = str(table.name or "").strip()
    if not name:
        return None

    cte_node = relation_extraction.cte_nodes.get(normalize_identifier_part(name))
    if cte_node is not None:
        return cte_node.id

    qualified_name = qualified_table_name(table)
    normalized_qualified_name = normalize_identifier(qualified_name)
    table_node = relation_extraction.table_nodes.get(normalized_qualified_name)
    if table_node is not None:
        return table_node.id

    raise QueryMapContractError(
        f"Unresolved relation id for table reference {qualified_name!r}."
    )
