"""Value-lineage filtering and predicate classification."""

from __future__ import annotations

from typing import Any, Literal

from clearmetric.core import normalize_identifier, normalize_identifier_part
from sqlglot import exp
from sqlglot.lineage import Node as SqlglotLineageNode

from .errors import LineageContractError
from .sql_parse import SqlStatementAnalysis, analyze_sql_statement

PredicateUsage = Literal["join", "filter", "grouping", "ordering"]

def defining_value_expression(root: SqlglotLineageNode) -> Any:
    """Return the shallowest downstream expression that requires value filtering."""
    filter_expr = _value_filter_expression(root)
    if filter_expr is not None:
        return filter_expr
    expression = root.expression
    if expression is not None and hasattr(expression, "args"):
        return unwrap_alias(expression)
    raise LineageContractError("Lineage root is missing a defining expression.")

def _unwrap_to_case_or_if(expression: Any) -> Any | None:
    """Return the Case/If expression under casts/aliases, if any."""
    current = unwrap_alias(expression)
    while isinstance(current, exp.Cast):
        current = unwrap_alias(current.this)
    return current if isinstance(current, (exp.Case, exp.If)) else None

def _case_branches_carry_column_values(expression: Any) -> bool:
    """True when CASE/IF result branches reference columns instead of literals only."""
    case_or_if = _unwrap_to_case_or_if(expression)
    if case_or_if is None:
        return False
    branch_exprs: list[Any] = []
    if isinstance(case_or_if, exp.Case):
        for branch in case_or_if.args.get("ifs") or []:
            if isinstance(branch, exp.If):
                branch_exprs.append(branch.args.get("true"))
            else:
                branch_exprs.append(branch)
        branch_exprs.append(case_or_if.args.get("default"))
    elif isinstance(case_or_if, exp.If):
        branch_exprs.extend(
            [case_or_if.args.get("true"), case_or_if.args.get("false")]
        )
    for branch_expr in branch_exprs:
        unwrapped = unwrap_alias(branch_expr)
        if isinstance(unwrapped, exp.Cast):
            unwrapped = unwrap_alias(unwrapped.this)
        if isinstance(unwrapped, exp.Column):
            return True
    return False

def _lineage_leaf_column_names(
    node: SqlglotLineageNode,
    *,
    _seen: set[int] | None = None,
) -> set[str]:
    if _seen is None:
        _seen = set()
    node_id = id(node)
    if node_id in _seen:
        return set()
    _seen.add(node_id)
    names: set[str] = set()
    expression = getattr(node, "expression", None)
    unwrapped = unwrap_alias(expression) if expression is not None else None
    if isinstance(unwrapped, exp.Column) and unwrapped.name:
        names.add(normalize_identifier_part(unwrapped.name))
    for child in node.downstream:
        names.update(_lineage_leaf_column_names(child, _seen=_seen))
    return names

def expand_predicate_columns_from_lineage(
    root: SqlglotLineageNode,
    predicate_column_names: set[str],
) -> set[str]:
    """Include upstream leaf columns for predicate names resolved in the lineage tree."""
    expanded = set(predicate_column_names)
    for name in predicate_column_names:
        key = normalize_identifier_part(name)
        for child in root.downstream:
            child_key = normalize_identifier_part(child.name.split(".")[-1])
            if child_key == key:
                expanded.update(_lineage_leaf_column_names(child))
    return expanded

def _case_pass_through_output_names(case_expr: Any) -> set[str]:
    """Output column names passed through verbatim in Case result branches."""
    unwrapped = _unwrap_to_case_or_if(case_expr)
    if not isinstance(unwrapped, exp.Case):
        return set()
    names: set[str] = set()
    for branch in unwrapped.args.get("ifs") or []:
        if isinstance(branch, exp.If):
            true_expr = unwrap_alias(branch.args.get("true"))
        else:
            true_expr = unwrap_alias(branch)
        if isinstance(true_expr, exp.Column) and true_expr.name:
            names.add(normalize_identifier_part(true_expr.name))
    default_expr = unwrap_alias(unwrapped.args.get("default"))
    if isinstance(default_expr, exp.Column) and default_expr.name:
        names.add(normalize_identifier_part(default_expr.name))
    return names

def _merge_pass_through_case_value_columns(
    root: SqlglotLineageNode,
    filter_expr: Any,
    value_columns: set[exp.Column],
) -> None:
    """Merge predicate columns from inner Cases referenced by pass-through branches."""
    pass_through_names = _case_pass_through_output_names(filter_expr)
    if not pass_through_names:
        return
    for child in root.downstream:
        child_key = normalize_identifier_part(child.name.split(".")[-1])
        if child_key not in pass_through_names:
            continue
        inner_filter = _value_filter_expression(child)
        if inner_filter is None:
            continue
        value_columns.update(
            _value_columns(
                inner_filter,
                include_case_predicates=_unwrap_to_case_or_if(inner_filter) is not None,
            )
        )

def filter_value_lineage_refs(
    root: SqlglotLineageNode,
    selected_refs: set[str],
    *,
    dialect: str,
    analysis: SqlStatementAnalysis | None = None,
) -> set[str]:
    """Filter lineage refs down to value-carrying columns only."""
    del dialect  # reserved for future dialect-specific filtering
    filter_expr = _value_filter_expression(root)
    root_expr = unwrap_alias(root.expression) if root.expression is not None else None
    check_expr = unwrap_alias(filter_expr) if filter_expr is not None else root_expr
    if check_expr is not None:
        surrogate_refs = _surrogate_key_value_refs(check_expr)
        if surrogate_refs is not None:
            return {
                ref
                for ref in selected_refs
                if _matches_value_ref(
                    ref,
                    allowed_refs=set(),
                    allowed_column_names=set(surrogate_refs),
                )
            }
    if filter_expr is None:
        return selected_refs
    case_or_if = _unwrap_to_case_or_if(filter_expr)
    include_case_predicates = case_or_if is not None and not _case_branches_carry_column_values(
        filter_expr
    )
    value_columns = _value_columns(
        filter_expr,
        include_case_predicates=include_case_predicates,
    )
    _merge_pass_through_case_value_columns(root, filter_expr, value_columns)
    allowed_refs = {
        normalize_identifier(column.sql())
        for column in value_columns
        if _is_qualified_column(column)
    }
    predicate_names = {
        normalize_identifier_part(column.name)
        for column in value_columns
        if column.name
    }
    allowed_column_names = expand_predicate_columns_from_lineage(
        root,
        predicate_names,
    )
    if analysis is not None:
        from . import sql_analyzer as _sa

        for name in predicate_names:
            allowed_column_names.update(
                _sa.projection_source_column_names(name, analysis=analysis)
            )
        if not include_case_predicates:
            for nested_case in _sa.nested_case_expressions(filter_expr):
                allowed_column_names.update(
                    _sa._case_cross_relation_predicate_names(
                        nested_case,
                        analysis=analysis,
                    )
                )
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
    seen_nodes: set[int] = set()

    def visit(node: SqlglotLineageNode, depth: int) -> None:
        nonlocal best, best_depth
        node_id = id(node)
        if node_id in seen_nodes:
            return
        seen_nodes.add(node_id)
        expression = node.expression
        if expression is None or not hasattr(expression, "args"):
            return
        unwrapped = unwrap_alias(expression)
        if _is_value_defining_expression(expression) and _needs_value_filter(unwrapped):
            if depth < best_depth:
                best = expression
                best_depth = depth
        for child in node.downstream:
            visit(child, depth + 1)

    visit(root, 0)
    return unwrap_alias(best) if best is not None else None

def _value_columns(
    node: Any,
    *,
    include_case_predicates: bool = False,
    _seen: set[int] | None = None,
) -> set[exp.Column]:
    columns: set[exp.Column] = set()
    if node is None or not hasattr(node, "args"):
        return columns
    if _seen is None:
        _seen = set()
    node_id = id(node)
    if node_id in _seen:
        return columns
    _seen.add(node_id)
    if isinstance(node, exp.Column):
        columns.add(node)
        return columns
    if isinstance(node, exp.Case):
        for branch in node.args.get("ifs") or []:
            if isinstance(branch, exp.If):
                if include_case_predicates:
                    columns.update(
                        _value_columns(
                            branch.args.get("this"),
                            include_case_predicates=include_case_predicates,
                            _seen=_seen,
                        )
                    )
                columns.update(
                    _value_columns(
                        branch.args.get("true"),
                        include_case_predicates=include_case_predicates,
                        _seen=_seen,
                    )
                )
            else:
                columns.update(
                    _value_columns(
                        branch,
                        include_case_predicates=include_case_predicates,
                        _seen=_seen,
                    )
                )
        columns.update(
            _value_columns(
                node.args.get("default"),
                include_case_predicates=include_case_predicates,
                _seen=_seen,
            )
        )
        return columns
    if isinstance(node, exp.If):
        if include_case_predicates:
            columns.update(
                _value_columns(
                    node.args.get("this"),
                    include_case_predicates=include_case_predicates,
                    _seen=_seen,
                )
            )
        columns.update(
            _value_columns(
                node.args.get("true"),
                include_case_predicates=include_case_predicates,
                _seen=_seen,
            )
        )
        columns.update(
            _value_columns(
                node.args.get("false"),
                include_case_predicates=include_case_predicates,
                _seen=_seen,
            )
        )
        return columns
    if isinstance(node, exp.Join):
        columns.update(
            _value_columns(
                node.args.get("this"),
                include_case_predicates=include_case_predicates,
                _seen=_seen,
            )
        )
        return columns
    if isinstance(node, (exp.Where, exp.Having)):
        return columns
    if isinstance(node, exp.Window):
        columns.update(
            _value_columns(
                node.args.get("this"),
                include_case_predicates=include_case_predicates,
                _seen=_seen,
            )
        )
        return columns
    for child in node.args.values():
        if isinstance(child, list):
            for item in child:
                columns.update(
                    _value_columns(
                        item,
                        include_case_predicates=include_case_predicates,
                        _seen=_seen,
                    )
                )
            continue
        columns.update(
            _value_columns(
                child,
                include_case_predicates=include_case_predicates,
                _seen=_seen,
            )
        )
    return columns

def _hash_inner_expression(expression: Any) -> Any | None:
    unwrapped = unwrap_alias(expression)
    if isinstance(unwrapped, exp.Cast):
        return _hash_inner_expression(unwrapped.this)
    if isinstance(unwrapped, exp.MD5):
        return unwrapped.this
    if isinstance(unwrapped, exp.Anonymous) and str(unwrapped.this).lower() in {
        "md5",
        "hash",
    }:
        expressions = unwrapped.expressions
        if expressions:
            return expressions[0]
    return None

def _surrogate_key_value_refs(expression: Any) -> frozenset[str] | None:
    """When output is an md5 hash key, keep only calendar year/month value refs."""
    inner = _hash_inner_expression(expression)
    if inner is None:
        return None
    hash_columns = _value_columns(inner, include_case_predicates=False)
    hash_names = {
        normalize_identifier_part(column.name)
        for column in hash_columns
        if column.name
    }
    if not hash_names:
        return None
    if "year_month" in hash_names or {"year", "month"} & hash_names:
        return frozenset({"year", "month", "year_month"})
    return None

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

def _needs_value_filter(node: Any, *, _seen: set[int] | None = None) -> bool:
    if node is None or not hasattr(node, "iter_expressions"):
        return False
    if _seen is None:
        _seen = set()
    node_id = id(node)
    if node_id in _seen:
        return False
    _seen.add(node_id)
    if isinstance(node, (exp.Case, exp.Where, exp.Having)):
        return True
    if isinstance(node, exp.Window):
        return bool(node.args.get("partition_by"))
    return any(
        _needs_value_filter(child, _seen=_seen) for child in node.iter_expressions()
    )

def unwrap_alias(node: Any) -> Any:
    if node is None or not hasattr(node, "args"):
        return node
    if isinstance(node, exp.Alias):
        return unwrap_alias(node.this)
    return node

def _is_value_defining_expression(node: Any) -> bool:
    if node is None or not hasattr(node, "args"):
        return False
    expression = unwrap_alias(node)
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
    if isinstance(expression, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.DPipe)):
        return True
    if isinstance(expression, exp.Cast):
        return _is_value_defining_expression(expression.this)
    return _needs_value_filter(expression)

def classify_predicate_usage(
    compiled_sql: str,
    edge: tuple[str, str, str, str],
    *,
    dialect: str,
) -> PredicateUsage | None:
    """Trace/triage diagnostic: column referenced only in predicate clauses."""
    _upstream_table, upstream_col, _dest_table, _dest_col = edge
    analysis = analyze_sql_statement(compiled_sql, dialect=dialect)
    statement = analysis.statement
    col_key = normalize_identifier_part(upstream_col)

    if _column_in_select_outputs(statement, col_key):
        return None

    return _column_predicate_usage(
        statement,
        col_key,
        alias_map=analysis.alias_map,
    )

def _column_in_select_outputs(statement: Any, column_key: str) -> bool:
    select = statement.find(exp.Select)
    if select is None:
        return False
    for expression in select.expressions:
        for column in expression.find_all(exp.Column):
            if normalize_identifier_part(column.name) == column_key:
                return True
    return False

def _column_predicate_usage(
    statement: Any,
    column_key: str,
    *,
    alias_map: dict[str, str],
) -> PredicateUsage | None:
    if _column_in_clause(
        statement,
        column_key,
        clause_types=(exp.Join,),
        alias_map=alias_map,
    ):
        return "join"
    if _column_in_clause(
        statement,
        column_key,
        clause_types=(exp.Where, exp.Having),
        alias_map=alias_map,
    ):
        return "filter"
    if _column_in_clause(
        statement,
        column_key,
        clause_types=(exp.Group,),
        alias_map=alias_map,
    ):
        return "grouping"
    if _column_in_clause(
        statement,
        column_key,
        clause_types=(exp.Order,),
        alias_map=alias_map,
    ):
        return "ordering"
    return None

def _column_in_clause(
    statement: Any,
    column_key: str,
    *,
    clause_types: tuple[type, ...],
    alias_map: dict[str, str],
) -> bool:
    for clause_type in clause_types:
        for clause in statement.find_all(clause_type):
            for column in clause.find_all(exp.Column):
                if normalize_identifier_part(column.name) != column_key:
                    continue
                table = column.table
                if isinstance(table, str) and table:
                    normalized_table = normalize_identifier_part(table)
                    if normalized_table not in alias_map:
                        raise ValueError(
                            f"Unknown table alias {table!r} in predicate diagnostic"
                        )
                return True
    return False

__all__ = [
    "PredicateUsage",
    "classify_predicate_usage",
    "defining_value_expression",
    "expand_predicate_columns_from_lineage",
    "filter_value_lineage_refs",
    "unwrap_alias",
]
