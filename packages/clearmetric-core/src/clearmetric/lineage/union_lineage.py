"""UNION branch resolution for macro-generated and structural unions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterator

from clearmetric.core import normalize_identifier, normalize_identifier_part
from sqlglot import exp

from .sql_parse import (
    SqlStatementAnalysis,
    cte_select_branches,
    outer_select,
    qualified_table_reference,
    select_expression_for_output,
)
from .star_expansion import select_alias_to_relation
from .value_lineage import unwrap_alias as _unwrap_alias

if TYPE_CHECKING:
    from .loaders import ProjectInput
    from .schema_registry import SchemaRegistry
else:
    ProjectInput = Any
    SchemaRegistry = Any

def is_null_literal_expression(expression: Any) -> bool:
    """True when a projection is NULL or CAST(NULL AS ...)."""
    current = _unwrap_alias(expression)
    if isinstance(current, exp.Null):
        return True
    if isinstance(current, exp.Cast):
        return is_null_literal_expression(current.this)
    return False

def is_macro_generated_union(analysis: SqlStatementAnalysis) -> bool:
    """True when compiled SQL uses star projections inside UNION branches."""
    if not analysis.has_union:
        return False
    for union in analysis.statement.find_all(exp.Union):
        for select in _union_branch_selects(union):
            for expression in select.expressions:
                inner = (
                    expression.this if isinstance(expression, exp.Alias) else expression
                )
                if isinstance(inner, exp.Star):
                    return True
    return False

def _union_branch_selects(node: Any) -> Iterator[exp.Select]:
    """Yield leaf SELECT nodes from a sqlglot UNION tree."""
    if isinstance(node, exp.Select):
        yield node
        return
    if isinstance(node, exp.Union):
        yield from _union_branch_selects(node.this)
        yield from _union_branch_selects(node.expression)
        return
    if isinstance(node, (exp.Subquery, exp.Paren, exp.Query)):
        if node.this is not None:
            yield from _union_branch_selects(node.this)
        return

def macro_union_branch_base_relations(
    analysis: SqlStatementAnalysis,
) -> tuple[str, ...]:
    """Return base-table FROM refs for each branch of a dbt_utils-style star UNION."""
    if not is_macro_generated_union(analysis):
        return ()
    relations: list[str] = []
    seen: set[str] = set()
    cte_names = analysis.cte_names
    for union in analysis.statement.find_all(exp.Union):
        for select in _union_branch_selects(union):
            from_clause = select.args.get("from_") or select.args.get("from")
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
            if normalize_identifier_part(table.name) in cte_names:
                continue
            reference = qualified_table_reference(table)
            ref_key = normalize_identifier(reference)
            if ref_key in seen:
                continue
            seen.add(ref_key)
            relations.append(reference)
    return tuple(relations)

def outer_union_cte_first_branch_refs(
    output_name: str,
    *,
    analysis: SqlStatementAnalysis,
) -> set[str]:
    """When the outer SELECT reads a single UNION CTE, return first-branch upstream refs."""
    outer = outer_select(analysis.statement)
    if not isinstance(outer, exp.Select):
        return set()
    from_clause = outer.args.get("from_") or outer.args.get("from")
    if from_clause is None:
        return set()
    tables = [
        table
        for table in from_clause.find_all(exp.Table)
        if isinstance(table, exp.Table)
    ]
    if len(tables) != 1:
        return set()
    table = tables[0]
    cte_name = normalize_identifier_part(table.name or "")
    if cte_name not in analysis.cte_names:
        return set()
    branches = cte_select_branches(cte_name, analysis=analysis)
    if len(branches) < 2:
        return set()
    ref = _branch_column_ref(
        branches[0],
        output_name=output_name,
        analysis=analysis,
    )
    return {ref} if ref else set()

def _root_union_branch_selects(analysis: SqlStatementAnalysis) -> tuple[exp.Select, ...]:
    """Return ordered leaf SELECT branches for a top-level UNION statement."""
    if isinstance(analysis.statement, exp.Union):
        return tuple(_union_branch_selects(analysis.statement))
    return ()

def _branch_column_ref(
    select: exp.Select,
    *,
    output_name: str,
    analysis: SqlStatementAnalysis,
) -> str | None:
    expression = select_expression_for_output(select, output_name)
    if expression is None:
        return None
    if is_null_literal_expression(expression):
        return None
    unwrapped = _unwrap_alias(expression)
    if isinstance(unwrapped, exp.Cast):
        unwrapped = _unwrap_alias(unwrapped.this)
    if isinstance(unwrapped, exp.Star):
        return None
    if not isinstance(unwrapped, exp.Column):
        return None
    column_name = unwrapped.name
    if not column_name:
        return None
    alias_map = select_alias_to_relation(select, analysis=analysis)
    table = unwrapped.args.get("table")
    if isinstance(table, exp.Identifier):
        source_alias = normalize_identifier_part(table.this)
        relation = alias_map.get(source_alias, source_alias)
    elif len(alias_map) == 1:
        relation = next(iter(alias_map.values()))
    else:
        return None
    return normalize_identifier(f"{relation}.{normalize_identifier_part(column_name)}")

def _union_branch_output_names(analysis: SqlStatementAnalysis) -> frozenset[str]:
    """Output column names shared across root UNION branch SELECT lists."""
    branches = _root_union_branch_selects(analysis)
    if not branches:
        return frozenset()
    names: set[str] = set()
    for expression in branches[0].expressions:
        inner = expression.this if isinstance(expression, exp.Alias) else expression
        if isinstance(inner, exp.Star):
            continue
        alias = expression.alias_or_name
        if alias and alias != "*":
            names.add(normalize_identifier_part(alias))
        elif isinstance(_unwrap_alias(expression), exp.Column):
            column = _unwrap_alias(expression)
            if isinstance(column, exp.Column) and column.name:
                names.add(normalize_identifier_part(column.name))
    return frozenset(names)

def union_has_null_padding_asymmetry(analysis: SqlStatementAnalysis) -> bool:
    """True when UNION branches mix NULL literals and column refs for the same output."""
    branches = _root_union_branch_selects(analysis)
    if len(branches) < 2:
        return False
    for output_name in _union_branch_output_names(analysis):
        has_null = False
        has_column = False
        for select in branches:
            expression = select_expression_for_output(select, output_name)
            if expression is None:
                continue
            if is_null_literal_expression(expression):
                has_null = True
                continue
            unwrapped = _unwrap_alias(expression)
            if isinstance(unwrapped, exp.Cast):
                unwrapped = _unwrap_alias(unwrapped.this)
            if isinstance(unwrapped, exp.Column):
                has_column = True
        if has_null and has_column:
            return True
    return False

def union_branch_column_refs(
    output_name: str,
    *,
    analysis: SqlStatementAnalysis,
) -> set[str]:
    """Resolve UNION branch upstreams with null-padding and final-branch preference."""
    branches = _root_union_branch_selects(analysis)
    if not branches:
        return set()
    branch_refs: list[tuple[int, str]] = []
    for index, select in enumerate(branches):
        ref = _branch_column_ref(select, output_name=output_name, analysis=analysis)
        if ref is not None:
            branch_refs.append((index, ref))
    if not branch_refs:
        return set()
    final_index = len(branches) - 1
    active_indices = {index for index, _ in branch_refs}
    if len(active_indices) == 1 and final_index not in active_indices:
        return set()
    return {ref for index, ref in branch_refs if index == final_index}

def macro_union_schema_branch_refs_index(
    *,
    analysis: SqlStatementAnalysis,
    registry: SchemaRegistry,
    project: ProjectInput,
    resolve_canonical_parent: Callable[[str], str | None],
    parent_is_allowed: Callable[[str], bool],
) -> dict[str, set[str]]:
    """Index macro-union schema branch refs for all columns in one branch scan."""
    from collections import defaultdict

    from .schema_registry import MissingSchema, UnknownRelation

    if not is_macro_generated_union(analysis):
        return {}
    refs_by_column: dict[str, set[str]] = defaultdict(set)
    for base_relation in macro_union_branch_base_relations(analysis):
        canonical = resolve_canonical_parent(base_relation)
        if canonical is None or canonical not in project.datasets:
            continue
        if not parent_is_allowed(canonical):
            continue
        upstream = registry.resolve_relation(base_relation, alias_map=analysis.alias_map)
        if isinstance(upstream, UnknownRelation | MissingSchema):
            column_names = {
                normalize_identifier_part(column)
                for column in project.datasets[canonical].declared_columns
            }
        else:
            column_names = upstream.column_names
        for column in column_names:
            refs_by_column[column].add(normalize_identifier(f"{canonical}.{column}"))
    return dict(refs_by_column)

def macro_union_schema_branch_refs(
    output_name: str,
    *,
    analysis: SqlStatementAnalysis,
    registry: SchemaRegistry,
    project: ProjectInput,
    resolve_canonical_parent: Callable[[str], str | None],
    parent_is_allowed: Callable[[str], bool],
    schema_refs_index: dict[str, set[str]] | None = None,
) -> set[str]:
    """Synthesize union branch refs from schema when star-only branches hide sqlglot lineage."""
    if schema_refs_index is not None:
        return set(schema_refs_index.get(normalize_identifier_part(output_name), set()))
    index = macro_union_schema_branch_refs_index(
        analysis=analysis,
        registry=registry,
        project=project,
        resolve_canonical_parent=resolve_canonical_parent,
        parent_is_allowed=parent_is_allowed,
    )
    return set(index.get(normalize_identifier_part(output_name), set()))

def union_branch_upstream_refs(
    output_name: str,
    *,
    analysis: SqlStatementAnalysis,
    sqlglot_branch_refs: set[str],
    registry: SchemaRegistry,
    project: ProjectInput,
    resolve_canonical_parent: Callable[[str], str | None],
    parent_is_allowed: Callable[[str], bool],
    macro_union_schema_refs_index: dict[str, set[str]] | None = None,
) -> set[str]:
    """Union sqlglot branch refs, structural UNION SELECT refs, and macro-star schema refs."""
    if isinstance(analysis.statement, exp.Union):
        if union_has_null_padding_asymmetry(analysis):
            return union_branch_column_refs(output_name, analysis=analysis)
        if is_macro_generated_union(analysis):
            schema_refs = macro_union_schema_branch_refs(
                output_name,
                analysis=analysis,
                registry=registry,
                project=project,
                resolve_canonical_parent=resolve_canonical_parent,
                parent_is_allowed=parent_is_allowed,
                schema_refs_index=macro_union_schema_refs_index,
            )
            if schema_refs:
                return schema_refs
        return set()
    if is_macro_generated_union(analysis):
        schema_refs = macro_union_schema_branch_refs(
            output_name,
            analysis=analysis,
            registry=registry,
            project=project,
            resolve_canonical_parent=resolve_canonical_parent,
            parent_is_allowed=parent_is_allowed,
            schema_refs_index=macro_union_schema_refs_index,
        )
        if schema_refs:
            return schema_refs
    refs = set(sqlglot_branch_refs)
    if not refs:
        refs.update(
            macro_union_schema_branch_refs(
                output_name,
                analysis=analysis,
                registry=registry,
                project=project,
                resolve_canonical_parent=resolve_canonical_parent,
                parent_is_allowed=parent_is_allowed,
                schema_refs_index=macro_union_schema_refs_index,
            )
        )
    return refs

__all__ = [
    "is_macro_generated_union",
    "macro_union_branch_base_relations",
    "macro_union_schema_branch_refs",
    "macro_union_schema_branch_refs_index",
    "outer_union_cte_first_branch_refs",
    "union_branch_column_refs",
    "union_branch_upstream_refs",
    "union_has_null_padding_asymmetry",
]
