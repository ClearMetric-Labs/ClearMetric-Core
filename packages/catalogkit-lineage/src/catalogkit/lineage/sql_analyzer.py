"""Small sqlglot helpers local to catalogkit-lineage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

import sqlglot
from catalogkit.core import (
    CanonicalIdError,
    normalize_identifier,
    normalize_identifier_part,
)
from sqlglot import exp
from sqlglot.lineage import Node as SqlglotLineageNode

from .errors import LineageContractError, LineageInputError

if TYPE_CHECKING:
    from .loaders import ProjectInput


@dataclass(frozen=True)
class SqlStatementAnalysis:
    """Single-parse SQL statement context shared by lineage edge resolution."""

    statement: Any
    alias_map: dict[str, str]
    cte_names: set[str]
    table_references: tuple[str, ...]
    has_union: bool


@dataclass(frozen=True)
class StarExpansionPolicy:
    """Which output columns are expanded from star projections under strict R6."""

    suppress_all_outputs: bool
    suppressed_output_names: frozenset[str]


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
            "catalogkit-lineage accepts exactly one SQL statement per project file."
        )
    return statements[0]


def analyze_sql_statement(sql: str, *, dialect: str) -> SqlStatementAnalysis:
    """Parse one SQL statement once and derive shared relation metadata."""
    statement = parse_single_statement(sql, dialect=dialect)
    return SqlStatementAnalysis(
        statement=statement,
        alias_map=_relation_alias_map(statement),
        cte_names=_cte_names(statement),
        table_references=_table_references(statement, dialect=dialect),
        has_union=statement.find(exp.Union) is not None,
    )


def has_select_star_projection(analysis: SqlStatementAnalysis) -> bool:
    """Return True when the outer SELECT list projects bare or qualified stars."""
    return any(_select_star_projections(analysis.statement))


def star_expansion_policy(
    analysis: SqlStatementAnalysis,
    *,
    project: ProjectInput,
) -> StarExpansionPolicy | None:
    """Return output suppression policy for select-star strict value-lineage (R6)."""
    has_bare_star = False
    qualified_star_aliases: list[str] = []

    for inner, qualified_alias in _select_star_projections(analysis.statement):
        if isinstance(inner, exp.Star) and inner.args.get("table") is None:
            has_bare_star = True
        elif qualified_alias is not None:
            qualified_star_aliases.append(qualified_alias)

    if has_bare_star:
        return StarExpansionPolicy(
            suppress_all_outputs=True,
            suppressed_output_names=frozenset(),
        )

    if not qualified_star_aliases:
        return None

    suppressed: set[str] = set()
    for alias in qualified_star_aliases:
        suppressed.update(
            _declared_columns_for_relation(
                alias,
                project=project,
                alias_map=analysis.alias_map,
            )
        )
    return StarExpansionPolicy(
        suppress_all_outputs=False,
        suppressed_output_names=frozenset(suppressed),
    )


def quoted_alias_output_columns(analysis: SqlStatementAnalysis) -> frozenset[str]:
    """Return outputs where quoting prevents confident value-lineage (strict R8)."""
    quoted: set[str] = set()
    for select in analysis.statement.find_all(exp.Select):
        for expression in select.expressions:
            alias_name = expression.alias_or_name
            if not alias_name or alias_name == "*":
                continue
            try:
                normalized_alias = normalize_identifier_part(alias_name)
            except CanonicalIdError:
                continue
            if isinstance(expression, exp.Alias):
                alias_node = expression.args.get("alias")
                if alias_node is not None and getattr(alias_node, "quoted", False):
                    quoted.add(normalized_alias)
                    continue
            inner = _unwrap_alias(expression)
            if isinstance(inner, exp.Column) and _column_identifier_is_quoted(inner):
                quoted.add(normalized_alias)
    return frozenset(quoted)


def _column_identifier_is_quoted(column: exp.Column) -> bool:
    identifier = column.this
    return isinstance(identifier, exp.Identifier) and bool(identifier.quoted)


def is_star_suppressed_output(
    output_name: str,
    policy: StarExpansionPolicy | None,
) -> bool:
    if policy is None:
        return False
    if policy.suppress_all_outputs:
        return True
    return normalize_identifier_part(output_name) in policy.suppressed_output_names


def _declared_columns_for_relation(
    relation_key: str,
    *,
    project: ProjectInput,
    alias_map: dict[str, str],
) -> set[str]:
    resolved = alias_map.get(relation_key, relation_key)
    for candidate in (resolved, relation_key):
        dependency = project.datasets.get(candidate)
        if dependency is not None and dependency.declared_columns:
            return {
                normalize_identifier_part(name) for name in dependency.declared_columns
            }
        root_schema = project.root_schema().get(candidate)
        if root_schema:
            return {normalize_identifier_part(name) for name in root_schema}
    return set()


def uses_aliased_table_star(analysis: SqlStatementAnalysis) -> bool:
    """Return True when the statement projects alias-qualified stars such as ``t.*``."""
    for _inner, qualified_alias in _select_star_projections(analysis.statement):
        if qualified_alias is not None and qualified_alias in analysis.alias_map:
            return True
    return False


def list_table_references(sql: str, *, dialect: str) -> list[str]:
    """Return normalized table references while excluding local CTE names."""
    return list(analyze_sql_statement(sql, dialect=dialect).table_references)


def _select_star_projections(statement: Any) -> Iterator[tuple[Any, str | None]]:
    """Yield (inner expression, qualified alias key) for each star projection in SELECT."""
    for select in statement.find_all(exp.Select):
        for expression in select.expressions:
            inner = _unwrap_alias(expression)
            if isinstance(inner, exp.Star) or (
                isinstance(inner, exp.Column) and inner.name == "*"
            ):
                yield inner, _qualified_star_table_key(inner)


def _qualified_star_table_key(inner: Any) -> str | None:
    table = inner.args.get("table")
    if table is None or not table.name:
        return None
    return normalize_identifier_part(table.name)


def _relation_alias_map(statement: Any) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        if not table.name:
            continue
        relation = normalize_identifier_part(table.name)
        if table.alias:
            mapping[normalize_identifier_part(table.alias)] = relation
    return mapping


def _cte_names(statement: Any) -> set[str]:
    return {
        normalize_identifier_part(cte.alias_or_name)
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _table_references(statement: Any, *, dialect: str) -> tuple[str, ...]:
    cte_names = {
        normalize_identifier(cte.alias_or_name)
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    references: list[str] = []
    seen: set[str] = set()
    for table in statement.find_all(exp.Table):
        reference = normalize_identifier(table.sql(dialect=dialect))
        if reference in cte_names or reference in seen:
            continue
        seen.add(reference)
        references.append(reference)
    return tuple(references)


def defining_value_expression(root: SqlglotLineageNode) -> Any:
    """Return the shallowest downstream expression that requires value filtering."""
    filter_expr = _value_filter_expression(root)
    if filter_expr is not None:
        return filter_expr
    expression = root.expression
    if expression is not None and hasattr(expression, "args"):
        return _unwrap_alias(expression)
    raise LineageContractError("Lineage root is missing a defining expression.")


def filter_value_lineage_refs(
    root: SqlglotLineageNode,
    selected_refs: set[str],
    *,
    dialect: str,
) -> set[str]:
    """Filter lineage refs down to value-carrying columns only."""
    del dialect  # reserved for future dialect-specific filtering
    filter_expr = _value_filter_expression(root)
    if filter_expr is None:
        return selected_refs
    value_columns = _value_columns(filter_expr)
    allowed_refs = {
        normalize_identifier(column.sql())
        for column in value_columns
        if _is_qualified_column(column)
    }
    allowed_column_names = {
        normalize_identifier_part(column.name)
        for column in value_columns
        if column.name
    }
    return {
        ref
        for ref in selected_refs
        if ref != "*"
        if _matches_value_ref(
            ref,
            allowed_refs=allowed_refs,
            allowed_column_names=allowed_column_names,
        )
    }


def _value_filter_expression(root: SqlglotLineageNode) -> Any:
    """Return the shallowest value-defining expression that needs predicate filtering."""
    best: Any = None
    best_depth = float("inf")

    def visit(node: SqlglotLineageNode, depth: int) -> None:
        nonlocal best, best_depth
        expression = node.expression
        if expression is None or not hasattr(expression, "args"):
            return
        unwrapped = _unwrap_alias(expression)
        if _is_value_defining_expression(expression) and _needs_value_filter(unwrapped):
            if depth < best_depth:
                best = expression
                best_depth = depth
        for child in node.downstream:
            visit(child, depth + 1)

    visit(root, 0)
    return _unwrap_alias(best) if best is not None else None


def _value_columns(node: Any) -> set[exp.Column]:
    columns: set[exp.Column] = set()
    if node is None or not hasattr(node, "args"):
        return columns
    if isinstance(node, exp.Column):
        columns.add(node)
        return columns
    if isinstance(node, exp.Case):
        for branch in node.args.get("ifs") or []:
            if isinstance(branch, exp.If):
                columns.update(_value_columns(branch.args.get("true")))
            else:
                columns.update(_value_columns(branch))
        columns.update(_value_columns(node.args.get("default")))
        return columns
    if isinstance(node, exp.If):
        columns.update(_value_columns(node.args.get("true")))
        columns.update(_value_columns(node.args.get("false")))
        return columns
    if isinstance(node, exp.Join):
        columns.update(_value_columns(node.args.get("this")))
        return columns
    if isinstance(node, (exp.Where, exp.Having)):
        return columns
    if isinstance(node, exp.Window):
        columns.update(_value_columns(node.args.get("this")))
        return columns
    for child in node.args.values():
        if isinstance(child, list):
            for item in child:
                columns.update(_value_columns(item))
            continue
        columns.update(_value_columns(child))
    return columns


def _is_qualified_column(node: exp.Column) -> bool:
    table = node.args.get("table")
    return isinstance(table, exp.Identifier) and bool(table.this)


def _matches_value_ref(
    reference: str,
    *,
    allowed_refs: set[str],
    allowed_column_names: set[str],
) -> bool:
    normalized_ref = normalize_identifier(reference)
    if normalized_ref in allowed_refs:
        return True
    parts = normalized_ref.split(".")
    if not parts:
        return False
    return parts[-1] in allowed_column_names


def _needs_value_filter(node: Any) -> bool:
    if node is None or not hasattr(node, "iter_expressions"):
        return False
    if isinstance(node, (exp.Case, exp.Where, exp.Having)):
        return True
    if isinstance(node, exp.Window):
        return bool(node.args.get("partition_by"))
    return any(_needs_value_filter(child) for child in node.iter_expressions())


def _unwrap_alias(node: Any) -> Any:
    if node is None or not hasattr(node, "args"):
        return node
    if isinstance(node, exp.Alias):
        return _unwrap_alias(node.this)
    return node


def _is_value_defining_expression(node: Any) -> bool:
    if node is None or not hasattr(node, "args"):
        return False
    expression = _unwrap_alias(node)
    if isinstance(
        expression,
        (exp.Select, exp.Subquery, exp.Query, exp.Union, exp.Join, exp.Star, exp.Table),
    ):
        return False
    if isinstance(expression, exp.Column):
        return False
    if isinstance(expression, exp.AggFunc):
        return True
    if isinstance(expression, (exp.Case, exp.If, exp.Coalesce, exp.Nullif)):
        return True
    if isinstance(expression, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)):
        return True
    if isinstance(expression, exp.Cast):
        return _is_value_defining_expression(expression.this)
    return _needs_value_filter(expression)
