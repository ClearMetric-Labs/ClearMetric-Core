"""Star expansion policy and star-suppressed column resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from collections.abc import Iterator

from clearmetric.core import CanonicalIdError, normalize_identifier, normalize_identifier_part
from sqlglot import exp
from sqlglot.lineage import Node as SqlglotLineageNode

from .sql_parse import (
    SqlStatementAnalysis,
    cte_select_body,
    from_clause_base_relation_instances,
    outer_select,
    select_expression_for_output,
)
from .value_lineage import _is_qualified_column, unwrap_alias

if TYPE_CHECKING:
    from .loaders import ProjectDataset
    from .schema_registry import SchemaRegistry
else:
    ProjectDataset = Any
    SchemaRegistry = Any


def select_alias_to_relation(
    select: exp.Select,
    *,
    analysis: SqlStatementAnalysis,
) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    from_clause = select.args.get("from_") or select.args.get("from")
    if from_clause is not None:
        for table in from_clause.find_all(exp.Table):
            if not table.name:
                continue
            alias = normalize_identifier_part(table.alias_or_name or table.name)
            relation = normalize_identifier_part(table.name)
            alias_map[alias] = relation
    for join in select.args.get("joins") or []:
        for table in join.find_all(exp.Table):
            if not table.name:
                continue
            alias = normalize_identifier_part(table.alias_or_name or table.name)
            relation = normalize_identifier_part(table.name)
            alias_map[alias] = relation
    return alias_map


def relation_in_closure(relation: str, allowed_relations: set[str]) -> bool:
    relation_key = normalize_identifier_part(relation)
    if relation in allowed_relations or relation_key in allowed_relations:
        return True
    for allowed in allowed_relations:
        if normalize_identifier_part(allowed.split(".")[-1]) == relation_key:
            return True
    return False


def declared_columns_for_relation(
    relation_key: str,
    *,
    schema: dict[str, dict[str, str]],
    datasets: dict[str, ProjectDataset],
    alias_map: dict[str, str],
) -> set[str]:
    resolved = alias_map.get(relation_key, relation_key)
    for candidate in (resolved, relation_key):
        dependency = datasets.get(candidate)
        if dependency is not None and dependency.declared_columns:
            return {
                normalize_identifier_part(name) for name in dependency.declared_columns
            }
        typed = schema.get(candidate)
        if typed:
            return {normalize_identifier_part(name) for name in typed}
    return set()


def typed_columns_for_relation(
    relation_key: str,
    *,
    schema: dict[str, dict[str, str]],
    datasets: dict[str, ProjectDataset],
    alias_map: dict[str, str],
) -> set[str]:
    resolved = alias_map.get(relation_key, relation_key)
    for candidate in (resolved, relation_key):
        typed = schema.get(candidate)
        if typed:
            return {normalize_identifier_part(name) for name in typed}
    return set()


def qualified_star_table_key(inner: Any) -> str | None:
    table = inner.args.get("table")
    if table is None or not table.name:
        return None
    return normalize_identifier_part(table.name)


def select_star_projections(
    statement: Any,
    *,
    outer_only: bool = False,
) -> Iterator[tuple[Any, str | None]]:
    if outer_only:
        select = outer_select(statement)
        selects = [select] if isinstance(select, exp.Select) else []
    else:
        selects = list(statement.find_all(exp.Select))
    for select in selects:
        for expression in select.expressions:
            inner = unwrap_alias(expression)
            if isinstance(inner, exp.Star) or (
                isinstance(inner, exp.Column) and inner.name == "*"
            ):
                yield inner, qualified_star_table_key(inner)


def bare_star_from_multiple_relations(analysis: SqlStatementAnalysis) -> bool:
    from_tables = from_clause_base_relation_instances(analysis)
    return len(from_tables) > 1


def column_identifier_is_quoted(column: exp.Column) -> bool:
    identifier = column.this
    return isinstance(identifier, exp.Identifier) and bool(identifier.quoted)


def expression_traces_outside_relations(
    expression: Any,
    select: exp.Select,
    *,
    analysis: SqlStatementAnalysis,
    allowed_relations: set[str],
    visited_projections: frozenset[tuple[str, str]],
) -> bool:
    alias_map = select_alias_to_relation(select, analysis=analysis)
    default_relation: str | None = None
    if len(alias_map) == 1:
        default_relation = next(iter(alias_map.values()))
    for column in expression.find_all(exp.Column):
        table = column.args.get("table")
        if isinstance(table, exp.Identifier):
            alias = normalize_identifier_part(table.this)
            relation = alias_map.get(alias, alias)
        elif default_relation is not None:
            relation = default_relation
        else:
            continue
        column_name = normalize_identifier_part(column.name or "")
        if relation in analysis.cte_names:
            projection_key = (relation, column_name)
            if projection_key in visited_projections:
                continue
            if cte_projected_column_traces_outside_relations(
                relation,
                column_name,
                analysis=analysis,
                allowed_relations=allowed_relations,
                visited_projections=visited_projections | {projection_key},
            ):
                return True
            continue
        if not relation_in_closure(relation, allowed_relations):
            return True
    return False


def expression_column_refs(
    expression: Any,
    *,
    analysis: SqlStatementAnalysis,
    select: exp.Select | None = None,
) -> frozenset[tuple[str, str]]:
    """Collect direct column references from a projection expression."""
    alias_map = (
        select_alias_to_relation(select, analysis=analysis)
        if select is not None
        else analysis.alias_map
    )
    default_relation: str | None = None
    if len(alias_map) == 1:
        default_relation = next(iter(alias_map.values()))
    refs: set[tuple[str, str]] = set()
    for column in expression.find_all(exp.Column):
        column_name = column.name
        if not column_name or normalize_identifier_part(column_name) == "*":
            continue
        table = column.args.get("table")
        if isinstance(table, exp.Identifier):
            source_alias = normalize_identifier_part(table.this)
            relation = alias_map.get(source_alias, source_alias)
        elif default_relation is not None:
            relation = default_relation
        else:
            continue
        refs.add((relation, normalize_identifier_part(column_name)))
    return frozenset(refs)


@dataclass(frozen=True)
class StarExpansionPolicy:
    """Which output columns are expanded from star projections under strict R6."""

    suppress_all_outputs: bool
    suppressed_output_names: frozenset[str]

def star_expansion_policy(
    analysis: SqlStatementAnalysis,
    *,
    schema: dict[str, dict[str, str]],
    datasets: dict[str, ProjectDataset],
    alias_map: dict[str, str] | None = None,
) -> StarExpansionPolicy | None:
    """Return output suppression policy for select-star strict value-lineage (R6)."""
    if alias_map is None:
        alias_map = analysis.alias_map
    has_bare_star = False
    qualified_star_aliases: list[str] = []

    for inner, qualified_alias in select_star_projections(
        analysis.statement,
        outer_only=True,
    ):
        if isinstance(inner, exp.Star) and inner.args.get("table") is None:
            has_bare_star = True
        elif qualified_alias is not None:
            qualified_star_aliases.append(qualified_alias)

    if has_bare_star:
        if bare_star_from_multiple_relations(analysis):
            return StarExpansionPolicy(
                suppress_all_outputs=True,
                suppressed_output_names=frozenset(),
            )
        source_relation = single_bare_star_source_relation(analysis)
        if source_relation is None:
            return StarExpansionPolicy(
                suppress_all_outputs=True,
                suppressed_output_names=frozenset(),
            )
        typed_columns = typed_columns_for_relation(
            source_relation,
            schema=schema,
            datasets=datasets,
            alias_map=alias_map,
        )
        declared_columns = declared_columns_for_relation(
            source_relation,
            schema=schema,
            datasets=datasets,
            alias_map=alias_map,
        )
        if typed_columns and typed_columns == declared_columns:
            return None
        if typed_columns and typed_columns != declared_columns:
            missing = declared_columns - typed_columns
            return StarExpansionPolicy(
                suppress_all_outputs=False,
                suppressed_output_names=frozenset(missing),
            )
        if declared_columns:
            return None
        return StarExpansionPolicy(
            suppress_all_outputs=True,
            suppressed_output_names=frozenset(),
        )

    if not qualified_star_aliases:
        return None

    outer_qualified_star_aliases: list[str] = []
    for _inner, qualified_alias in select_star_projections(
        analysis.statement,
        outer_only=True,
    ):
        if qualified_alias is not None:
            outer_qualified_star_aliases.append(qualified_alias)
    if not outer_qualified_star_aliases:
        return None

    suppressed: set[str] = set()
    for alias in outer_qualified_star_aliases:
        suppressed.update(
            declared_columns_for_relation(
                alias,
                schema=schema,
                datasets=datasets,
                alias_map=alias_map,
            )
        )
    return StarExpansionPolicy(
        suppress_all_outputs=False,
        suppressed_output_names=frozenset(suppressed),
    )

def bare_star_column_upstream(
    output_name: str,
    *,
    analysis: SqlStatementAnalysis,
    schema: dict[str, dict[str, str]],
    datasets: dict[str, ProjectDataset],
    registry: SchemaRegistry | None = None,
) -> tuple[str, str] | None:
    """Return upstream dataset/column for a bare-star output when schema is known."""
    has_outer_bare_star = any(
        isinstance(inner, exp.Star) and inner.args.get("table") is None
        for inner, _qualified_alias in select_star_projections(
            analysis.statement,
            outer_only=True,
        )
    )
    if not has_outer_bare_star:
        return None
    if bare_star_from_multiple_relations(analysis):
        return None
    source_relation = single_bare_star_source_relation(analysis)
    if source_relation is None:
        return None
    normalized_output = normalize_identifier_part(output_name)
    resolved_parent = analysis.alias_map.get(source_relation, source_relation)

    if registry is not None:
        from .schema_registry import MissingSchema, UnknownRelation

        upstream = registry.resolve_relation(
            resolved_parent,
            alias_map=analysis.alias_map,
        )
        if isinstance(upstream, UnknownRelation | MissingSchema):
            return None
        if normalized_output not in upstream.column_names:
            return None
        parent_id = upstream.relation_id
        return parent_id, normalized_output

    declared_columns = declared_columns_for_relation(
        source_relation,
        schema=schema,
        datasets=datasets,
        alias_map=analysis.alias_map,
    )
    typed_columns = typed_columns_for_relation(
        source_relation,
        schema=schema,
        datasets=datasets,
        alias_map=analysis.alias_map,
    )
    if not declared_columns or typed_columns != declared_columns:
        return None
    if normalized_output not in declared_columns:
        return None
    return resolved_parent, normalized_output

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
            inner = unwrap_alias(expression)
            if isinstance(inner, exp.Column) and column_identifier_is_quoted(inner):
                quoted.add(normalized_alias)
    return frozenset(quoted)

def explicit_outer_select_output_columns(analysis: SqlStatementAnalysis) -> frozenset[str]:
    """Outputs projected by non-star expressions in the outer SELECT."""
    explicit: set[str] = set()
    select = outer_select(analysis.statement)
    if not isinstance(select, exp.Select):
        return frozenset()
    for expression in select.expressions:
        inner = unwrap_alias(expression)
        if isinstance(inner, exp.Star) or (
            isinstance(inner, exp.Column) and inner.name == "*"
        ):
            continue
        alias_name = expression.alias_or_name
        if not alias_name or alias_name == "*":
            continue
        try:
            explicit.add(normalize_identifier_part(alias_name))
        except CanonicalIdError:
            continue
    return frozenset(explicit)

def lineage_root_is_qualified_column_reference(root: SqlglotLineageNode) -> bool:
    """True when the output lineage root is a single qualified column reference."""
    expression = getattr(root, "expression", None)
    if expression is None:
        return False
    unwrapped = unwrap_alias(expression)
    return isinstance(unwrapped, exp.Column) and _is_qualified_column(unwrapped)


def lineage_root_is_qualified_cte_column_reference(
    root: SqlglotLineageNode,
    *,
    cte_names: set[str],
) -> bool:
    """True when the output lineage root is ``cte_name.column``."""
    expression = getattr(root, "expression", None)
    if expression is None:
        return False
    unwrapped = unwrap_alias(expression)
    if not isinstance(unwrapped, exp.Column) or not _is_qualified_column(unwrapped):
        return False
    table = unwrapped.args.get("table")
    if not isinstance(table, exp.Identifier):
        return False
    return normalize_identifier_part(table.this) in cte_names


def is_star_suppressed_output(
    output_name: str,
    policy: StarExpansionPolicy | None,
    *,
    statement_analysis: SqlStatementAnalysis | None = None,
    lineage_root: SqlglotLineageNode | None = None,
) -> bool:
    if policy is None:
        return False
    if statement_analysis is not None:
        if normalize_identifier_part(output_name) in explicit_outer_select_output_columns(
            statement_analysis
        ):
            return False
    if policy.suppress_all_outputs:
        return True
    return normalize_identifier_part(output_name) in policy.suppressed_output_names


def mixed_explicit_and_star_outer_select(
    statement_analysis: SqlStatementAnalysis,
) -> bool:
    """True when outer SELECT lists explicit projections and bare-star expansion."""
    if not has_select_star_projection(statement_analysis):
        return False
    return bool(explicit_outer_select_output_columns(statement_analysis))

def has_select_star_projection(analysis: SqlStatementAnalysis) -> bool:
    """Return True when the outer SELECT list projects bare or qualified stars."""
    return any(select_star_projections(analysis.statement, outer_only=True))

def cte_projected_column_traces_outside_relations(
    cte_name: str,
    column_name: str,
    *,
    analysis: SqlStatementAnalysis,
    allowed_relations: set[str],
    visited_projections: frozenset[tuple[str, str]] | None = None,
) -> bool:
    """True when a CTE column's defining expression reads outside ``allowed_relations``."""
    if visited_projections is None:
        visited_projection: frozenset[tuple[str, str]] = frozenset()
    else:
        visited_projection = visited_projections
    projection_key = (
        normalize_identifier_part(cte_name),
        normalize_identifier_part(column_name),
    )
    if projection_key in visited_projection:
        return False
    visited_projection = visited_projection | {projection_key}
    select = cte_select_body(cte_name, analysis=analysis)
    if select is None:
        return False
    expression = select_expression_for_output(select, column_name)
    if expression is None:
        return False
    return expression_traces_outside_relations(
        expression,
        select,
        analysis=analysis,
        allowed_relations=allowed_relations,
        visited_projections=visited_projection,
    )


def cte_output_uses_secondary_join_source(
    cte_name: str,
    column_name: str,
    *,
    analysis: SqlStatementAnalysis,
) -> bool:
    """True when a CTE output column is projected from a non-primary join branch."""
    select = cte_select_body(cte_name, analysis=analysis)
    if select is None:
        return False
    expression = select_expression_for_output(select, column_name)
    unwrapped = unwrap_alias(expression)
    if not isinstance(unwrapped, exp.Column):
        return False
    table = unwrapped.args.get("table")
    if not isinstance(table, exp.Identifier):
        return False
    source_alias = normalize_identifier_part(table.this)
    from_clause = select.args.get("from_") or select.args.get("from")
    if from_clause is None:
        return False
    primary_alias: str | None = None
    for table_node in from_clause.find_all(exp.Table):
        if not table_node.name:
            continue
        primary_alias = normalize_identifier_part(
            table_node.alias_or_name or table_node.name
        )
        break
    if primary_alias is None:
        return False
    return source_alias != primary_alias


def star_suppressed_cte_column_resolves_outside_dependencies(
    output_name: str,
    lineage_root: SqlglotLineageNode,
    *,
    statement_analysis: SqlStatementAnalysis,
    allowed_relations: set[str],
) -> bool:
    """Allow star-suppressed CTE pass-through when the column leaves the dbt dep closure."""
    if not lineage_root_is_qualified_cte_column_reference(
        lineage_root,
        cte_names=statement_analysis.cte_names,
    ):
        return False
    expression = unwrap_alias(lineage_root.expression)
    if not isinstance(expression, exp.Column):
        return False
    table = expression.args.get("table")
    if not isinstance(table, exp.Identifier):
        return False
    return cte_projected_column_traces_outside_relations(
        normalize_identifier_part(table.this),
        normalize_identifier_part(output_name),
        analysis=statement_analysis,
        allowed_relations=allowed_relations,
    )

def single_bare_star_source_relation(analysis: SqlStatementAnalysis) -> str | None:
    from_tables = from_clause_base_relation_instances(analysis)
    if len(from_tables) != 1:
        return None
    return from_tables[0]

def uses_aliased_table_star(analysis: SqlStatementAnalysis) -> bool:
    """Return True when the statement projects alias-qualified stars such as ``t.*``."""
    for _inner, qualified_alias in select_star_projections(analysis.statement):
        if qualified_alias is not None and qualified_alias in analysis.alias_map:
            return True
    return False


def uses_outer_aliased_table_star(analysis: SqlStatementAnalysis) -> bool:
    """Return True when the outer SELECT projects alias-qualified stars such as ``t.*``."""
    for _inner, qualified_alias in select_star_projections(
        analysis.statement,
        outer_only=True,
    ):
        if qualified_alias is not None and qualified_alias in analysis.alias_map:
            return True
    return False


def qualified_star_alias_keys(analysis: SqlStatementAnalysis) -> frozenset[str]:
    """Alias keys that project qualified stars such as ``encounter.*`` anywhere in the statement."""
    keys: set[str] = set()
    for _inner, qualified_alias in select_star_projections(analysis.statement):
        if qualified_alias is not None:
            keys.add(qualified_alias)
    return frozenset(keys)

@dataclass(frozen=True)
class StarSuppressedUpstream:
    """Resolved upstream for a star-suppressed output column."""

    parent_id: str
    source_column: str
    via_cte_passthrough: bool


def resolve_star_suppressed_column_upstream(
    output_name: str,
    *,
    analysis: SqlStatementAnalysis,
    root: SqlglotLineageNode,
    registry: SchemaRegistry,
    schema: dict[str, dict[str, str]],
    datasets: dict[str, ProjectDataset],
    allowed_relations: set[str],
) -> StarSuppressedUpstream | None:
    """Resolve star-suppressed output to upstream column or eligible CTE passthrough."""
    outer_star_cte = single_bare_star_source_relation(analysis)
    allow_cte_passthrough = (
        mixed_explicit_and_star_outer_select(analysis)
        and outer_star_cte is not None
        and (
            star_suppressed_cte_column_resolves_outside_dependencies(
                output_name,
                root,
                statement_analysis=analysis,
                allowed_relations=allowed_relations,
            )
            or cte_output_uses_secondary_join_source(
                outer_star_cte,
                output_name,
                analysis=analysis,
            )
        )
    )
    if allow_cte_passthrough and outer_star_cte is not None:
        select = cte_select_body(outer_star_cte, analysis=analysis)
        if select is not None:
            expression = select_expression_for_output(select, output_name)
            if expression is not None:
                for relation, column in expression_column_refs(
                    unwrap_alias(expression),
                    analysis=analysis,
                    select=select,
                ):
                    upstream = registry.resolve_relation(
                        relation,
                        alias_map=analysis.alias_map,
                    )
                    from .schema_registry import RelationSchema

                    if isinstance(upstream, RelationSchema):
                        return StarSuppressedUpstream(
                            parent_id=upstream.relation_id,
                            source_column=column,
                            via_cte_passthrough=True,
                        )
    registry_upstream = bare_star_column_upstream(
        output_name,
        analysis=analysis,
        schema=schema,
        datasets=datasets,
        registry=registry,
    )
    if registry_upstream is None:
        return None
    parent_id, source_column = registry_upstream
    return StarSuppressedUpstream(
        parent_id=parent_id,
        source_column=source_column,
        via_cte_passthrough=False,
    )

__all__ = [
    "StarExpansionPolicy",
    "StarSuppressedUpstream",
    "bare_star_column_upstream",
    "cte_output_uses_secondary_join_source",
    "cte_projected_column_traces_outside_relations",
    "explicit_outer_select_output_columns",
    "has_select_star_projection",
    "is_star_suppressed_output",
    "lineage_root_is_qualified_column_reference",
    "lineage_root_is_qualified_cte_column_reference",
    "mixed_explicit_and_star_outer_select",
    "qualified_star_alias_keys",
    "quoted_alias_output_columns",
    "resolve_star_suppressed_column_upstream",
    "single_bare_star_source_relation",
    "star_expansion_policy",
    "star_suppressed_cte_column_resolves_outside_dependencies",
    "uses_aliased_table_star",
    "uses_outer_aliased_table_star",
]

