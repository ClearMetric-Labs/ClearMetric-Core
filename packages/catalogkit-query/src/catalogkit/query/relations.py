"""Relation discovery for catalogkit-query."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from catalogkit.core import Node, cte_id, leaf_name, normalize_identifier, normalize_identifier_part, schema_name, table_id
from sqlglot import exp

from .ast_utils import (
    cte_name,
    has_join_ancestor,
    iter_ctes,
    iter_table_nodes,
    iter_table_nodes_skipping_ctes,
    qualified_table_name,
)
from .models import Relation

RelationUsageContext = Literal["from", "join", "cte_body"]


@dataclass(frozen=True)
class RelationUsage:
    relation_id: str
    alias: str | None
    context: RelationUsageContext
    sql: str


@dataclass(frozen=True)
class RelationExtraction:
    nodes: list[Node]
    relations: list[Relation]
    relation_usages: list[RelationUsage]
    cte_nodes: dict[str, Node]
    table_nodes: dict[str, Node]
    relation_by_id: dict[str, Relation]


def extract_relations(root_expression: exp.Expression, *, dialect: str) -> RelationExtraction:
    """Extract canonical table and CTE nodes plus usage evidence."""
    cte_nodes = _extract_cte_nodes(root_expression)
    cte_relations = _extract_cte_relations(root_expression)
    table_nodes: dict[str, Node] = {}
    relation_by_id: dict[str, Relation] = {
        relation.id: relation for relation in cte_relations.values()
    }
    relation_usages: list[RelationUsage] = []

    for cte in iter_ctes(root_expression):
        for table in iter_table_nodes(cte.this):
            node = _resolve_node(
                table,
                cte_nodes=cte_nodes,
                table_nodes=table_nodes,
                relation_by_id=relation_by_id,
                cte_relations=cte_relations,
            )
            if node is None:
                continue
            relation_usages.append(
                _build_usage(
                    relation_id=node.id,
                    table=table,
                    context="join" if has_join_ancestor(table, stop_node=cte.this) else "cte_body",
                    dialect=dialect,
                )
            )

    for table in iter_table_nodes_skipping_ctes(root_expression):
        node = _resolve_node(
            table,
            cte_nodes=cte_nodes,
            table_nodes=table_nodes,
            relation_by_id=relation_by_id,
            cte_relations=cte_relations,
        )
        if node is None:
            continue
        relation_usages.append(
            _build_usage(
                relation_id=node.id,
                table=table,
                context="join" if has_join_ancestor(table, stop_node=root_expression) else "from",
                dialect=dialect,
            )
        )

    nodes = sorted([*table_nodes.values(), *cte_nodes.values()], key=lambda node: node.id)
    relations = sorted(relation_by_id.values(), key=lambda relation: relation.id)
    return RelationExtraction(
        nodes=nodes,
        relations=relations,
        relation_usages=_dedupe_usages(relation_usages),
        cte_nodes=cte_nodes,
        table_nodes=table_nodes,
        relation_by_id=relation_by_id,
    )


def _extract_cte_nodes(root_expression: exp.Expression) -> dict[str, Node]:
    nodes: dict[str, Node] = {}
    for cte in iter_ctes(root_expression):
        raw_name = cte_name(cte)
        if not raw_name:
            continue
        normalized_name = normalize_identifier_part(raw_name)
        node = Node(
            id=cte_id(raw_name),
            kind="cte",
            name=normalized_name,
        )
        nodes[normalized_name] = node
    return nodes


def _extract_cte_relations(root_expression: exp.Expression) -> dict[str, Relation]:
    relations: dict[str, Relation] = {}
    for cte in iter_ctes(root_expression):
        raw_name = cte_name(cte)
        if not raw_name:
            continue
        normalized_name = normalize_identifier_part(raw_name)
        relations[normalized_name] = Relation(
            id=cte_id(raw_name),
            kind="cte",
            name=raw_name,
        )
    return relations


def _resolve_node(
    table: exp.Table,
    *,
    cte_nodes: dict[str, Node],
    table_nodes: dict[str, Node],
    relation_by_id: dict[str, Relation],
    cte_relations: dict[str, Relation],
) -> Node | None:
    table_name = str(table.name or "").strip()
    if not table_name:
        return None

    normalized_table_name = normalize_identifier_part(table_name)
    cte_node = cte_nodes.get(normalized_table_name)
    if cte_node is not None:
        relation_by_id[cte_node.id] = cte_relations[normalized_table_name]
        return cte_node

    qualified_name = qualified_table_name(table)
    normalized_qualified_name = normalize_identifier(qualified_name)
    node = table_nodes.get(normalized_qualified_name)
    if node is None:
        raw_schema_name = _raw_schema_name(table)
        node = Node(
            id=table_id(qualified_name),
            kind="table",
            name=leaf_name(qualified_name),
            qualified_name=normalized_qualified_name,
            schema=schema_name(qualified_name),
        )
        table_nodes[normalized_qualified_name] = node
        relation_by_id[node.id] = Relation(
            id=node.id,
            kind="table",
            name=table_name,
            qualified_name=qualified_name,
            schema=raw_schema_name,
        )
    return node


def _build_usage(
    *,
    relation_id: str,
    table: exp.Table,
    context: RelationUsageContext,
    dialect: str,
) -> RelationUsage:
    return RelationUsage(
        relation_id=relation_id,
        alias=str(table.alias or "").strip() or None,
        context=context,
        sql=table.sql(dialect=dialect),
    )


def _dedupe_usages(usages: list[RelationUsage]) -> list[RelationUsage]:
    seen: set[tuple[str, str | None, str, str]] = set()
    deduped: list[RelationUsage] = []
    for usage in usages:
        key = (usage.relation_id, usage.alias, usage.context, usage.sql)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(usage)
    return deduped


def _raw_schema_name(table: exp.Table) -> str | None:
    parts = [
        str(part).strip()
        for part in (table.catalog, table.db)
        if str(part or "").strip()
    ]
    if not parts:
        return None
    return ".".join(parts)
