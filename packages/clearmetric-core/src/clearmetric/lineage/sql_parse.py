"""SQL parsing and relation metadata for clearmetric lineage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from clearmetric.core import normalize_identifier, normalize_identifier_part
from sqlglot import exp

from .errors import LineageContractError, LineageInputError


@dataclass(frozen=True)
class SqlStatementAnalysis:
    """Single-parse SQL statement context shared by lineage edge resolution."""

    statement: Any
    alias_map: dict[str, str]
    cte_names: set[str]
    table_references: tuple[str, ...]
    has_union: bool


def parse_single_statement(sql: str, *, dialect: str) -> Any:
    """Parse exactly one SQL statement for lineage package analysis."""
    cleaned = (sql or "").strip()
    if not cleaned:
        raise LineageInputError("SQL input is empty.")

    try:
        statements = [
            statement
            for statement in sqlglot.parse(cleaned, read=dialect)
            if statement is not None
        ]
    except Exception as exc:  # pragma: no cover - exercised by caller failure paths
        raise LineageInputError(
            f"Failed to parse SQL with dialect {dialect!r}: {exc}"
        ) from exc

    if not statements:
        raise LineageInputError("SQL input produced no parseable statements.")
    if len(statements) != 1:
        raise LineageContractError(
            "clearmetric-core accepts exactly one SQL statement per project file."
        )
    return statements[0]


def analyze_sql_statement(sql: str, *, dialect: str) -> SqlStatementAnalysis:
    """Parse one SQL statement once and derive shared relation metadata."""
    statement = parse_single_statement(sql, dialect=dialect)
    return SqlStatementAnalysis(
        statement=statement,
        alias_map=relation_alias_map(statement),
        cte_names=cte_names(statement),
        table_references=table_references(statement, dialect=dialect),
        has_union=statement.find(exp.Union) is not None,
    )


def outer_select(statement: Any) -> exp.Select | None:
    """Outermost SELECT that defines model outputs (after WITH wrapper)."""
    if isinstance(statement, exp.With):
        inner = statement.this
        if isinstance(inner, exp.Select):
            return inner
    if isinstance(statement, exp.Select):
        return statement
    if isinstance(statement, exp.Query):
        inner = statement.this
        if isinstance(inner, exp.Select):
            return inner
    return None


def relation_alias_map(statement: Any) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        if not table.name:
            continue
        relation = qualified_table_reference(table)
        leaf = normalize_identifier_part(table.name)
        if table.alias:
            mapping[normalize_identifier_part(table.alias)] = relation
        mapping.setdefault(leaf, relation)
    return mapping


def cte_names(statement: Any) -> set[str]:
    return {
        normalize_identifier_part(cte.alias_or_name)
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }


def table_references(statement: Any, *, dialect: str) -> tuple[str, ...]:
    del dialect
    local_cte_names = cte_names(statement)
    references: list[str] = []
    seen: set[str] = set()
    for table in statement.find_all(exp.Table):
        reference = qualified_table_reference(table)
        ref_key = normalize_identifier_part(reference.split(".")[-1])
        if ref_key in local_cte_names or reference in seen:
            continue
        seen.add(reference)
        references.append(reference)
    return tuple(references)


def list_table_references(sql: str, *, dialect: str) -> list[str]:
    """Return normalized table references while excluding local CTE names."""
    return list(analyze_sql_statement(sql, dialect=dialect).table_references)


def qualified_table_reference(table: exp.Table) -> str:
    """Sql-visible `<database>.<schema>.<table>` from sqlglot table parts."""
    parts: list[str] = []
    for part in table.parts:
        segment = getattr(part, "name", None) or str(part)
        cleaned = normalize_identifier_part(str(segment).strip('"'))
        if cleaned:
            parts.append(cleaned)
    if parts:
        return normalize_identifier(".".join(parts))
    if table.name:
        return normalize_identifier_part(table.name)
    raise LineageInputError("SQL table reference is missing a name.")


def from_clause_base_relations(analysis: SqlStatementAnalysis) -> list[str]:
    """Normalized relation names referenced by the outer FROM (excluding CTEs)."""
    relations: list[str] = []
    for relation in from_clause_base_relation_instances(analysis):
        if relation not in relations:
            relations.append(relation)
    return relations


def from_clause_base_relation_instances(analysis: SqlStatementAnalysis) -> list[str]:
    """Normalized outer FROM/JOIN relation instances, preserving duplicate aliases."""
    select = outer_select(analysis.statement)
    if not isinstance(select, exp.Select):
        return []
    from_clause = select.args.get("from_") or select.args.get("from")
    if from_clause is None:
        from_clause = select.find(exp.From)
    if from_clause is None:
        return []
    relations: list[str] = []
    tables = list(from_clause.find_all(exp.Table))
    for join in select.args.get("joins") or []:
        tables.extend(join.find_all(exp.Table))
    for table in tables:
        if not table.name:
            continue
        relations.append(normalize_identifier_part(table.name))
    return relations


def cte_select_branches(
    cte_name: str,
    *,
    analysis: SqlStatementAnalysis,
) -> tuple[exp.Select, ...]:
    """Return SELECT branches composing a CTE body (handles UNION CTEs)."""
    target = normalize_identifier_part(cte_name)
    for cte in analysis.statement.find_all(exp.CTE):
        if normalize_identifier_part(cte.alias_or_name) != target:
            continue
        body = cte.this
        if isinstance(body, exp.Union):
            from .union_lineage import _union_branch_selects

            return tuple(_union_branch_selects(body))
        if isinstance(body, exp.Select):
            return (body,)
    return ()


def cte_select_body(
    cte_name: str,
    *,
    analysis: SqlStatementAnalysis,
) -> exp.Select | None:
    branches = cte_select_branches(cte_name, analysis=analysis)
    return branches[0] if branches else None


def select_expression_for_output(
    select: exp.Select,
    output_name: str,
) -> Any | None:
    """Return the defining expression for an output column name in a SELECT."""
    from .value_lineage import unwrap_alias

    target = normalize_identifier_part(output_name)
    for expression in select.expressions:
        inner = expression.this if isinstance(expression, exp.Alias) else expression
        if isinstance(inner, exp.Star):
            continue
        alias = expression.alias_or_name
        if alias and alias != "*" and normalize_identifier_part(alias) == target:
            return unwrap_alias(expression)
        unwrapped = unwrap_alias(expression)
        if isinstance(unwrapped, exp.Column) and unwrapped.name:
            raw_name = str(unwrapped.name).strip()
            if raw_name == "*":
                continue
            column_key = normalize_identifier_part(raw_name)
            if column_key == target:
                return unwrapped
    return None


__all__ = [
    "SqlStatementAnalysis",
    "analyze_sql_statement",
    "cte_names",
    "cte_select_body",
    "cte_select_branches",
    "from_clause_base_relation_instances",
    "from_clause_base_relations",
    "list_table_references",
    "outer_select",
    "parse_single_statement",
    "qualified_table_reference",
    "relation_alias_map",
    "select_expression_for_output",
    "table_references",
]
