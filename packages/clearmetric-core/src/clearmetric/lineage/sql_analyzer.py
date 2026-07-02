"""Small sqlglot helpers local to clearmetric-core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterator, Literal, cast

import sqlglot
from clearmetric.core import (
    CanonicalIdError,
    normalize_identifier,
    normalize_identifier_part,
)
from sqlglot import exp
from sqlglot.lineage import Node as SqlglotLineageNode

from .errors import LineageContractError, LineageInputError

if TYPE_CHECKING:
    from .loaders import ProjectDataset, ProjectInput
    from .schema_registry import SchemaRegistry
else:
    ProjectInput = Any
    ProjectDataset = Any
    SchemaRegistry = Any


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
            "clearmetric-core accepts exactly one SQL statement per project file."
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


def has_select_star_projection(analysis: SqlStatementAnalysis) -> bool:
    """Return True when the outer SELECT list projects bare or qualified stars."""
    return any(_select_star_projections(analysis.statement, outer_only=True))


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

    for inner, qualified_alias in _select_star_projections(
        analysis.statement,
        outer_only=True,
    ):
        if isinstance(inner, exp.Star) and inner.args.get("table") is None:
            has_bare_star = True
        elif qualified_alias is not None:
            qualified_star_aliases.append(qualified_alias)

    if has_bare_star:
        if _bare_star_from_multiple_relations(analysis):
            return StarExpansionPolicy(
                suppress_all_outputs=True,
                suppressed_output_names=frozenset(),
            )
        source_relation = _single_bare_star_source_relation(analysis)
        if source_relation is None:
            return StarExpansionPolicy(
                suppress_all_outputs=True,
                suppressed_output_names=frozenset(),
            )
        typed_columns = _typed_columns_for_relation(
            source_relation,
            schema=schema,
            datasets=datasets,
            alias_map=alias_map,
        )
        declared_columns = _declared_columns_for_relation(
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
    for _inner, qualified_alias in _select_star_projections(
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
            _declared_columns_for_relation(
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
        for inner, _qualified_alias in _select_star_projections(
            analysis.statement,
            outer_only=True,
        )
    )
    if not has_outer_bare_star:
        return None
    if _bare_star_from_multiple_relations(analysis):
        return None
    source_relation = _single_bare_star_source_relation(analysis)
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

    declared_columns = _declared_columns_for_relation(
        source_relation,
        schema=schema,
        datasets=datasets,
        alias_map=analysis.alias_map,
    )
    typed_columns = _typed_columns_for_relation(
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
            inner = _unwrap_alias(expression)
            if isinstance(inner, exp.Column) and _column_identifier_is_quoted(inner):
                quoted.add(normalized_alias)
    return frozenset(quoted)


def _column_identifier_is_quoted(column: exp.Column) -> bool:
    identifier = column.this
    return isinstance(identifier, exp.Identifier) and bool(identifier.quoted)


def explicit_outer_select_output_columns(analysis: SqlStatementAnalysis) -> frozenset[str]:
    """Outputs projected by non-star expressions in the outer SELECT."""
    explicit: set[str] = set()
    select = _outer_select(analysis.statement)
    if not isinstance(select, exp.Select):
        return frozenset()
    for expression in select.expressions:
        inner = _unwrap_alias(expression)
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
    unwrapped = _unwrap_alias(expression)
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
    unwrapped = _unwrap_alias(expression)
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


def _cte_select_body(
    cte_name: str,
    *,
    analysis: SqlStatementAnalysis,
) -> exp.Select | None:
    target = normalize_identifier_part(cte_name)
    for cte in analysis.statement.find_all(exp.CTE):
        if normalize_identifier_part(cte.alias_or_name) == target:
            body = cte.this
            if isinstance(body, exp.Union):
                branches = tuple(_union_branch_selects(body))
                return branches[0] if branches else None
            if isinstance(body, exp.Select):
                return body
    return None


def _projection_source_column_names(
    column_name: str,
    *,
    analysis: SqlStatementAnalysis,
) -> set[str]:
    """Leaf column names referenced by a projected alias definition."""
    names: set[str] = set()
    column_key = normalize_identifier_part(column_name)
    for select in analysis.statement.find_all(exp.Select):
        expression = _select_expression_for_output(select, column_key)
        if expression is None:
            continue
        for column in expression.find_all(exp.Column):
            if not column.name or str(column.name).strip() == "*":
                continue
            names.add(normalize_identifier_part(column.name))
    return names


def _nested_case_expressions(expression: Any) -> tuple[exp.Case, ...]:
    """Return Case nodes driving value filtering for an expression."""
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
    outer = _outer_select(analysis.statement)
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
            _unwrap_alias(branch_expr),
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
            _unwrap_alias(predicate),
            analysis=analysis,
            select=select,
        ):
            if (
                column
                and normalize_identifier_part(relation) not in branch_relations
            ):
                names.add(normalize_identifier_part(column))
    return names


def _select_expression_for_output(
    select: exp.Select,
    output_name: str,
) -> Any | None:
    target = normalize_identifier_part(output_name)
    for expression in select.expressions:
        inner = expression.this if isinstance(expression, exp.Alias) else expression
        if isinstance(inner, exp.Star):
            continue
        alias = expression.alias_or_name
        if alias and alias != "*" and normalize_identifier_part(alias) == target:
            return _unwrap_alias(expression)
        unwrapped = _unwrap_alias(expression)
        if isinstance(unwrapped, exp.Column) and unwrapped.name:
            raw_name = str(unwrapped.name).strip()
            if raw_name == "*":
                continue
            column_key = normalize_identifier_part(raw_name)
            if column_key == target:
                return unwrapped
    return None


def _select_alias_to_relation(
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


def _expression_traces_outside_relations(
    expression: Any,
    select: exp.Select,
    *,
    analysis: SqlStatementAnalysis,
    allowed_relations: set[str],
    visited_projections: frozenset[tuple[str, str]],
) -> bool:
    alias_map = _select_alias_to_relation(select, analysis=analysis)
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
        if not _relation_in_closure(relation, allowed_relations):
            return True
    return False


def _relation_in_closure(relation: str, allowed_relations: set[str]) -> bool:
    relation_key = normalize_identifier_part(relation)
    if relation in allowed_relations or relation_key in allowed_relations:
        return True
    for allowed in allowed_relations:
        if normalize_identifier_part(allowed.split(".")[-1]) == relation_key:
            return True
    return False


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
    select = _cte_select_body(cte_name, analysis=analysis)
    if select is None:
        return False
    expression = _select_expression_for_output(select, column_name)
    if expression is None:
        return False
    return _expression_traces_outside_relations(
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
    select = _cte_select_body(cte_name, analysis=analysis)
    if select is None:
        return False
    expression = _select_expression_for_output(select, column_name)
    unwrapped = _unwrap_alias(expression)
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
    expression = _unwrap_alias(lineage_root.expression)
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


def _bare_star_from_multiple_relations(analysis: SqlStatementAnalysis) -> bool:
    """True when bare SELECT * reads from more than one base relation (e.g. joins)."""
    from_tables = _from_clause_base_relation_instances(analysis)
    return len(from_tables) > 1


def _single_bare_star_source_relation(analysis: SqlStatementAnalysis) -> str | None:
    from_tables = _from_clause_base_relation_instances(analysis)
    if len(from_tables) != 1:
        return None
    return from_tables[0]


def from_clause_base_relations(analysis: SqlStatementAnalysis) -> list[str]:
    """Normalized relation names referenced by the outer FROM (excluding CTEs)."""
    return _from_clause_base_relations(analysis)


def _from_clause_base_relations(analysis: SqlStatementAnalysis) -> list[str]:
    """Normalized base relations referenced by the outer FROM/JOINs."""
    relations: list[str] = []
    for relation in _from_clause_base_relation_instances(analysis):
        if relation not in relations:
            relations.append(relation)
    return relations


def _from_clause_base_relation_instances(analysis: SqlStatementAnalysis) -> list[str]:
    """Normalized outer FROM/JOIN relation instances, preserving duplicate aliases."""
    select = _outer_select(analysis.statement)
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


def _declared_columns_for_relation(
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


def _typed_columns_for_relation(
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


def uses_aliased_table_star(analysis: SqlStatementAnalysis) -> bool:
    """Return True when the statement projects alias-qualified stars such as ``t.*``."""
    for _inner, qualified_alias in _select_star_projections(analysis.statement):
        if qualified_alias is not None and qualified_alias in analysis.alias_map:
            return True
    return False


def uses_outer_aliased_table_star(analysis: SqlStatementAnalysis) -> bool:
    """Return True when the outer SELECT projects alias-qualified stars such as ``t.*``."""
    for _inner, qualified_alias in _select_star_projections(
        analysis.statement,
        outer_only=True,
    ):
        if qualified_alias is not None and qualified_alias in analysis.alias_map:
            return True
    return False


def qualified_star_alias_keys(analysis: SqlStatementAnalysis) -> frozenset[str]:
    """Alias keys that project qualified stars such as ``encounter.*`` anywhere in the statement."""
    keys: set[str] = set()
    for _inner, qualified_alias in _select_star_projections(analysis.statement):
        if qualified_alias is not None:
            keys.add(qualified_alias)
    return frozenset(keys)


def list_table_references(sql: str, *, dialect: str) -> list[str]:
    """Return normalized table references while excluding local CTE names."""
    return list(analyze_sql_statement(sql, dialect=dialect).table_references)


def _outer_select(statement: Any) -> exp.Select | None:
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


def _select_star_projections(
    statement: Any,
    *,
    outer_only: bool = False,
) -> Iterator[tuple[Any, str | None]]:
    """Yield (inner expression, qualified alias key) for each star projection in SELECT."""
    if outer_only:
        select = _outer_select(statement)
        selects = [select] if isinstance(select, exp.Select) else []
    else:
        selects = list(statement.find_all(exp.Select))
    for select in selects:
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


def _relation_alias_map(statement: Any) -> dict[str, str]:
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


def _shadowed_outer_select_aliases(
    analysis: SqlStatementAnalysis,
) -> dict[str, str]:
    """Return outer-SELECT alias bindings that differ from an inner scope with the same alias."""
    outer = _outer_select(analysis.statement)
    if not isinstance(outer, exp.Select):
        return {}
    global_map = _relation_alias_map(analysis.statement)
    outer_map = _select_alias_to_relation(outer, analysis=analysis)
    return {
        key: relation
        for key, relation in outer_map.items()
        if key in global_map
        and global_map[key] != relation
        and relation not in analysis.cte_names
        and _alias_override_preferred(global_map[key], relation)
    }


def _cte_names(statement: Any) -> set[str]:
    return {
        normalize_identifier_part(cte.alias_or_name)
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }


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
        if normalize_identifier_part(table.name) in analysis.cte_names:
            continue
        return qualified_table_reference(table)
    return None


def _table_references(statement: Any, *, dialect: str) -> tuple[str, ...]:
    del dialect
    cte_names = _cte_names(statement)
    references: list[str] = []
    seen: set[str] = set()
    for table in statement.find_all(exp.Table):
        reference = qualified_table_reference(table)
        ref_key = normalize_identifier_part(reference.split(".")[-1])
        if ref_key in cte_names or reference in seen:
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


def _unwrap_to_case_or_if(expression: Any) -> Any | None:
    """Return the Case/If expression under casts/aliases, if any."""
    current = _unwrap_alias(expression)
    while isinstance(current, exp.Cast):
        current = _unwrap_alias(current.this)
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
        unwrapped = _unwrap_alias(branch_expr)
        if isinstance(unwrapped, exp.Cast):
            unwrapped = _unwrap_alias(unwrapped.this)
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
    unwrapped = _unwrap_alias(expression) if expression is not None else None
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
            true_expr = _unwrap_alias(branch.args.get("true"))
        else:
            true_expr = _unwrap_alias(branch)
        if isinstance(true_expr, exp.Column) and true_expr.name:
            names.add(normalize_identifier_part(true_expr.name))
    default_expr = _unwrap_alias(unwrapped.args.get("default"))
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
    root_expr = _unwrap_alias(root.expression) if root.expression is not None else None
    check_expr = _unwrap_alias(filter_expr) if filter_expr is not None else root_expr
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
        for name in predicate_names:
            allowed_column_names.update(
                _projection_source_column_names(name, analysis=analysis)
            )
        if not include_case_predicates:
            for nested_case in _nested_case_expressions(filter_expr):
                allowed_column_names.update(
                    _case_cross_relation_predicate_names(
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
        unwrapped = _unwrap_alias(expression)
        if _is_value_defining_expression(expression) and _needs_value_filter(unwrapped):
            if depth < best_depth:
                best = expression
                best_depth = depth
        for child in node.downstream:
            visit(child, depth + 1)

    visit(root, 0)
    return _unwrap_alias(best) if best is not None else None


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
    unwrapped = _unwrap_alias(expression)
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
        return frozenset({"year", "month"})
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
    if isinstance(expression, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.DPipe)):
        return True
    if isinstance(expression, exp.Cast):
        return _is_value_defining_expression(expression.this)
    return _needs_value_filter(expression)


PredicateUsage = Literal["join", "filter", "grouping", "ordering"]


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


def _is_null_literal_expression(expression: Any) -> bool:
    """True when a projection is NULL or CAST(NULL AS ...)."""
    current = _unwrap_alias(expression)
    if isinstance(current, exp.Null):
        return True
    if isinstance(current, exp.Cast):
        return _is_null_literal_expression(current.this)
    return False


def _is_literal_expression(expression: Any) -> bool:
    """True when a projection is a literal value (including CAST(literal AS ...))."""
    current = _unwrap_alias(expression)
    if isinstance(current, (exp.Null, exp.Literal)):
        return True
    if isinstance(current, exp.Cast):
        return _is_literal_expression(current.this)
    return False


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
    select = _cte_select_body(cte_name, analysis=analysis)
    if select is None:
        return False
    expression = _select_expression_for_output(select, column_name)
    if expression is None:
        return False
    if _is_literal_expression(expression):
        return True
    unwrapped = _unwrap_alias(expression)
    if isinstance(unwrapped, exp.Cast):
        unwrapped = _unwrap_alias(unwrapped.this)
    if isinstance(unwrapped, exp.Column) and unwrapped.name:
        table = unwrapped.args.get("table")
        if isinstance(table, exp.Identifier):
            source_alias = normalize_identifier_part(table.this)
            source_cte = source_alias
            if select is not None:
                cte_alias_map = _select_alias_to_relation(select, analysis=analysis)
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
    expression = _select_expression_for_output(select, output_name)
    if expression is None:
        return None
    if _is_null_literal_expression(expression):
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
    alias_map = _select_alias_to_relation(select, analysis=analysis)
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
            expression = _select_expression_for_output(select, output_name)
            if expression is None:
                continue
            if _is_null_literal_expression(expression):
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


def expression_column_refs(
    expression: Any,
    *,
    analysis: SqlStatementAnalysis,
    select: exp.Select | None = None,
) -> frozenset[tuple[str, str]]:
    """Collect direct column references from a projection expression."""
    alias_map = (
        _select_alias_to_relation(select, analysis=analysis)
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
    outer_star_cte = _single_bare_star_source_relation(analysis)
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
        select = _cte_select_body(outer_star_cte, analysis=analysis)
        if select is not None:
            expression = _select_expression_for_output(select, output_name)
            if expression is not None:
                for relation, column in expression_column_refs(
                    _unwrap_alias(expression),
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
