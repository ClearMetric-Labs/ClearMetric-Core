"""Small sqlglot helpers local to clearmetric-core."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator, cast

from clearmetric.core import CanonicalIdError, normalize_identifier, normalize_identifier_part
from sqlglot import exp
from sqlglot.lineage import Node as SqlglotLineageNode

from .errors import LineageContractError, LineageInputError
from .sql_parse import (
    SqlStatementAnalysis,
    analyze_sql_statement,
    cte_select_body,
    cte_select_branches,
    from_clause_base_relation_instances,
    from_clause_base_relations,
    list_table_references,
    outer_select,
    parse_single_statement,
    qualified_table_reference,
    relation_alias_map,
    select_expression_for_output,
)
from .star_expansion import (
    StarExpansionPolicy,
    StarSuppressedUpstream,
    bare_star_column_upstream,
    cte_output_uses_secondary_join_source,
    cte_projected_column_traces_outside_relations,
    expression_column_refs,
    expression_traces_outside_relations,
    explicit_outer_select_output_columns,
    has_select_star_projection,
    is_star_suppressed_output,
    lineage_root_is_qualified_column_reference,
    lineage_root_is_qualified_cte_column_reference,
    mixed_explicit_and_star_outer_select,
    qualified_star_alias_keys,
    resolve_star_suppressed_column_upstream,
    select_alias_to_relation,
    select_star_projections,
    single_bare_star_source_relation,
    star_expansion_policy,
    star_suppressed_cte_column_resolves_outside_dependencies,
    uses_aliased_table_star,
    uses_outer_aliased_table_star,
    quoted_alias_output_columns,
)
from .union_lineage import (
    is_macro_generated_union,
    macro_union_branch_base_relations,
    macro_union_schema_branch_refs,
    macro_union_schema_branch_refs_index,
    outer_union_cte_first_branch_refs,
    union_branch_column_refs,
    union_branch_upstream_refs,
    union_has_null_padding_asymmetry,
)
from .value_lineage import (
    PredicateUsage,
    classify_predicate_usage,
    defining_value_expression,
    expand_predicate_columns_from_lineage,
    filter_value_lineage_refs,
    unwrap_alias,
)

if TYPE_CHECKING:
    from .loaders import ProjectDataset, ProjectInput
    from .schema_registry import SchemaRegistry
else:
    ProjectInput = Any
    ProjectDataset = Any
    SchemaRegistry = Any


def lineage_output_map(
    sql: str,
    *,
    schema: dict[str, dict[str, str]] | None,
    sources: dict[str, str] | None,
    dialect: str,
) -> dict[str, SqlglotLineageNode]:
    """Build per-output-column sqlglot lineage roots (sqlglot 30 compatible)."""
    from sqlglot.errors import SqlglotError
    from sqlglot.lineage import lineage
    from sqlglot.optimizer import build_scope, qualify

    expression = parse_single_statement(sql, dialect=dialect)
    if sources:
        expression = exp.expand(
            expression,
            {
                key: parse_single_statement(value, dialect=dialect)
                for key, value in sources.items()
            },
            dialect=dialect,
        )
    expression = qualify.qualify(
        expression,
        dialect=dialect,
        schema=cast("dict[str, object] | None", schema),
        validate_qualify_columns=False,
        identify=False,
    )
    scope = build_scope(expression)
    if scope is None:
        raise LineageInputError("Cannot build lineage output map: SQL must be SELECT.")

    output_map: dict[str, SqlglotLineageNode] = {}
    select_root: Any = scope.expression
    for select in select_root.selects:
        output_name = select.alias_or_name
        if not output_name:
            continue
        try:
            output_map[output_name] = lineage(
                output_name,
                expression,
                schema=schema,
                sources=sources,
                dialect=dialect,
                scope=scope,
            )
        except SqlglotError:
            continue
    if not output_map:
        raise LineageInputError("Lineage output map is empty after column analysis.")
    return output_map

def cte_single_source_base_relation(
    cte_name: str,
    *,
    analysis: SqlStatementAnalysis,
) -> str | None:
    """Return the sole base-table FROM relation when a CTE reads from one table only."""
    target = normalize_identifier_part(cte_name)
    for cte in analysis.statement.find_all(exp.CTE):
        alias = cte.alias_or_name
        if not alias or normalize_identifier_part(alias) != target:
            continue
        body = cte.this
        if isinstance(body, exp.Union):
            from .union_lineage import _union_branch_selects

            branches = tuple(_union_branch_selects(body))
            body = branches[0] if branches else None
        if not isinstance(body, exp.Select):
            continue
        from_clause = body.args.get("from_") or body.args.get("from")
        if from_clause is None:
            continue
        tables = [
            table
            for table in from_clause.find_all(exp.Table)
            if isinstance(table, exp.Table)
        ]
        if len(tables) != 1:
            continue
        table = tables[0]
        table_key = normalize_identifier_part(table.name or "")
        if table_key in analysis.cte_names:
            nested = cte_single_source_base_relation(
                table_key,
                analysis=analysis,
            )
            if nested is not None:
                return nested
            continue
        return qualified_table_reference(table)
    return None

def projection_source_column_names(
    column_name: str,
    *,
    analysis: SqlStatementAnalysis,
) -> set[str]:
    """Leaf column names referenced by a projected alias definition."""
    names: set[str] = set()
    column_key = normalize_identifier_part(column_name)
    for select in analysis.statement.find_all(exp.Select):
        expression = select_expression_for_output(select, column_key)
        if expression is None:
            continue
        for column in expression.find_all(exp.Column):
            if not column.name or str(column.name).strip() == "*":
                continue
            names.add(normalize_identifier_part(column.name))
    return names

def nested_case_expressions(expression: Any) -> tuple[exp.Case, ...]:
    """Return Case nodes driving value filtering for an expression."""
    from .value_lineage import _unwrap_to_case_or_if

    case_or_if = _unwrap_to_case_or_if(expression)
    if isinstance(case_or_if, exp.Case):
        return (case_or_if,)
    if expression is None or not hasattr(expression, "find_all"):
        return ()
    return tuple(expression.find_all(exp.Case))

def _case_cross_relation_predicate_names(
    case_or_if: exp.Case,
    *,
    analysis: SqlStatementAnalysis,
) -> set[str]:
    """CASE predicate columns that route to value branches on other relations."""
    outer = outer_select(analysis.statement)
    select = outer if isinstance(outer, exp.Select) else None
    branch_relations: set[str] = set()
    branch_exprs: list[Any] = []
    for branch in case_or_if.args.get("ifs") or []:
        if isinstance(branch, exp.If):
            branch_exprs.append(branch.args.get("true"))
        else:
            branch_exprs.append(branch)
    default_expr = case_or_if.args.get("default")
    if default_expr is not None:
        branch_exprs.append(default_expr)
    for branch_expr in branch_exprs:
        for relation, _column in expression_column_refs(
            unwrap_alias(branch_expr),
            analysis=analysis,
            select=select,
        ):
            branch_relations.add(normalize_identifier_part(relation))
    names: set[str] = set()
    for branch in case_or_if.args.get("ifs") or []:
        if not isinstance(branch, exp.If):
            continue
        predicate = branch.args.get("this")
        if predicate is None:
            continue
        for relation, column in expression_column_refs(
            unwrap_alias(predicate),
            analysis=analysis,
            select=select,
        ):
            if (
                column
                and normalize_identifier_part(relation) not in branch_relations
            ):
                names.add(normalize_identifier_part(column))
    return names

def cte_projected_column_is_literal(
    cte_name: str,
    column_name: str,
    *,
    analysis: SqlStatementAnalysis,
) -> bool:
    """True when a CTE output column is defined from a literal rather than upstream data."""
    return cte_projected_column_traces_to_literal(
        cte_name,
        column_name,
        analysis=analysis,
    )

def cte_projected_column_traces_to_literal(
    cte_name: str,
    column_name: str,
    *,
    analysis: SqlStatementAnalysis,
    _visited: frozenset[str] | None = None,
) -> bool:
    """True when a CTE column resolves to a literal through passthrough chains."""
    visited = _visited or frozenset()
    cte_key = normalize_identifier_part(cte_name)
    column_key = normalize_identifier_part(column_name)
    visit_key = f"{cte_key}.{column_key}"
    if visit_key in visited:
        return False
    visited = frozenset({*visited, visit_key})
    select = cte_select_body(cte_name, analysis=analysis)
    if select is None:
        return False
    expression = select_expression_for_output(select, column_name)
    if expression is None:
        return False
    if _is_literal_expression(expression):
        return True
    unwrapped = unwrap_alias(expression)
    if isinstance(unwrapped, exp.Cast):
        unwrapped = unwrap_alias(unwrapped.this)
    if isinstance(unwrapped, exp.Column) and unwrapped.name:
        table = unwrapped.args.get("table")
        if isinstance(table, exp.Identifier):
            source_alias = normalize_identifier_part(table.this)
            source_cte = source_alias
            if select is not None:
                cte_alias_map = select_alias_to_relation(select, analysis=analysis)
                source_cte = normalize_identifier_part(
                    cte_alias_map.get(source_alias, source_alias)
                )
            if source_cte in analysis.cte_names:
                return cte_projected_column_traces_to_literal(
                    source_cte,
                    unwrapped.name,
                    analysis=analysis,
                    _visited=visited,
                )
    return False

def _alias_override_preferred(global_relation: str, outer_relation: str) -> bool:
    """Prefer outer binding only when it is not a less-qualified name for the same table."""
    global_leaf = normalize_identifier_part(global_relation.split(".")[-1])
    outer_leaf = normalize_identifier_part(outer_relation.split(".")[-1])
    if global_leaf == outer_leaf:
        return False
    global_key = normalize_identifier(global_relation)
    outer_key = normalize_identifier(outer_relation)
    if global_key.endswith("." + outer_key):
        return False
    return True

def shadowed_outer_select_aliases(
    analysis: SqlStatementAnalysis,
) -> dict[str, str]:
    """Return outer-SELECT alias bindings that differ from an inner scope with the same alias."""
    outer = outer_select(analysis.statement)
    if not isinstance(outer, exp.Select):
        return {}
    global_map = relation_alias_map(analysis.statement)
    outer_map = select_alias_to_relation(outer, analysis=analysis)
    return {
        key: relation
        for key, relation in outer_map.items()
        if key in global_map
        and global_map[key] != relation
        and relation not in analysis.cte_names
        and _alias_override_preferred(global_map[key], relation)
    }

def _is_literal_expression(expression: Any) -> bool:
    """True when a projection is a literal value (including CAST(literal AS ...))."""
    current = unwrap_alias(expression)
    if isinstance(current, (exp.Null, exp.Literal)):
        return True
    if isinstance(current, exp.Cast):
        return _is_literal_expression(current.this)
    return False

__all__ = [
    "PredicateUsage",
    "SqlStatementAnalysis",
    "StarExpansionPolicy",
    "StarSuppressedUpstream",
    "analyze_sql_statement",
    "bare_star_column_upstream",
    "classify_predicate_usage",
    "cte_select_body",
    "cte_select_branches",
    "cte_output_uses_secondary_join_source",
    "cte_projected_column_is_literal",
    "cte_projected_column_traces_outside_relations",
    "cte_projected_column_traces_to_literal",
    "cte_single_source_base_relation",
    "defining_value_expression",
    "expand_predicate_columns_from_lineage",
    "explicit_outer_select_output_columns",
    "expression_column_refs",
    "filter_value_lineage_refs",
    "from_clause_base_relations",
    "has_select_star_projection",
    "is_macro_generated_union",
    "is_star_suppressed_output",
    "lineage_output_map",
    "lineage_root_is_qualified_column_reference",
    "lineage_root_is_qualified_cte_column_reference",
    "list_table_references",
    "macro_union_branch_base_relations",
    "macro_union_schema_branch_refs",
    "macro_union_schema_branch_refs_index",
    "mixed_explicit_and_star_outer_select",
    "nested_case_expressions",
    "outer_select",
    "outer_union_cte_first_branch_refs",
    "parse_single_statement",
    "projection_source_column_names",
    "qualified_star_alias_keys",
    "qualified_table_reference",
    "quoted_alias_output_columns",
    "resolve_star_suppressed_column_upstream",
    "select_expression_for_output",
    "shadowed_outer_select_aliases",
    "single_bare_star_source_relation",
    "star_expansion_policy",
    "star_suppressed_cte_column_resolves_outside_dependencies",
    "union_branch_column_refs",
    "union_branch_upstream_refs",
    "union_has_null_padding_asymmetry",
    "unwrap_alias",
    "uses_aliased_table_star",
    "uses_outer_aliased_table_star",
]

