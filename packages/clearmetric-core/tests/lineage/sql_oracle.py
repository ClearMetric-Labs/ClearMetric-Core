"""Independent SQL oracle helpers for committed fixture cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlglot
from clearmetric.core.ids import column_id
from clearmetric.lineage.loaders import load_project
from clearmetric.lineage.relations import is_canonical_relation_id
from clearmetric.lineage.sql_analyzer import qualified_table_reference
from sqlglot import exp

SourceRef = tuple[str, str]


def sql_cast_passthrough_oracle_edges(
    sql: str,
    *,
    downstream_model: str,
    dialect: str,
    upstream_output_dependencies: dict[SourceRef, set[SourceRef]] | None = None,
) -> set[tuple[str, str]]:
    """Derive value-lineage edges from explicit cast/column passthrough projections."""
    statement = sqlglot.parse_one(sql, read=dialect)
    if not isinstance(statement, exp.Select):
        raise ValueError("SQL oracle supports a single SELECT statement only.")

    alias_to_table = _relation_alias_map(statement)
    primary_upstream = _primary_upstream_table(statement)
    cte_output_sources = _cte_output_sources(statement, dialect=dialect)
    edges: set[tuple[str, str]] = set()
    for expression in statement.expressions:
        if isinstance(expression, exp.Alias):
            output_name = expression.alias
            if not output_name or _is_literal_projection(expression.this):
                continue
            inner = expression.this
        elif isinstance(expression, exp.Column):
            output_name = expression.name
            if not output_name:
                continue
            inner = expression
        else:
            continue
        source = _passthrough_source(
            inner,
            alias_to_table=alias_to_table,
            primary_upstream=primary_upstream,
        )
        sources = (
            {source}
            if source is not None
            else _expression_sources(
                inner,
                alias_to_table=alias_to_table,
                primary_upstream=primary_upstream,
            )
        )
        if not sources:
            continue
        expanded_sources: set[SourceRef] = set()
        for expression_source in sources:
            expanded_sources.update(
                _expand_source(
                    expression_source,
                    cte_output_sources=cte_output_sources,
                    upstream_output_dependencies=upstream_output_dependencies or {},
                )
            )
        for upstream_table, source_column in expanded_sources:
            edges.add(
                (
                    column_id(downstream_model, output_name),
                    column_id(upstream_table, source_column),
                )
            )
    return edges


def _expand_source(
    source: SourceRef,
    *,
    cte_output_sources: dict[SourceRef, set[SourceRef]],
    upstream_output_dependencies: dict[SourceRef, set[SourceRef]],
    seen: set[SourceRef] | None = None,
) -> set[SourceRef]:
    seen = set(seen or set())
    if source in seen:
        return {source}
    seen.add(source)
    cte_sources = cte_output_sources.get(source)
    if cte_sources:
        expanded: set[SourceRef] = set()
        for cte_source in cte_sources:
            expanded.update(
                _expand_source(
                    cte_source,
                    cte_output_sources=cte_output_sources,
                    upstream_output_dependencies=upstream_output_dependencies,
                    seen=seen,
                )
            )
        return expanded
    return upstream_output_dependencies.get(source, {source})


def _cte_output_sources(
    statement: exp.Select,
    *,
    dialect: str,
) -> dict[SourceRef, set[SourceRef]]:
    output_sources: dict[SourceRef, set[SourceRef]] = {}
    for cte in statement.find_all(exp.CTE):
        cte_name = cte.alias
        cte_query = cte.this
        if not cte_name or not isinstance(cte_query, exp.Select):
            continue
        cte_edges = sql_cast_passthrough_oracle_edges(
            cte_query.sql(dialect=dialect),
            downstream_model=cte_name,
            dialect=dialect,
        )
        for downstream, upstream in cte_edges:
            downstream_ref = _column_id_ref(downstream)
            upstream_ref = _column_id_ref(upstream)
            if downstream_ref is not None and upstream_ref is not None:
                output_sources.setdefault(downstream_ref, set()).add(upstream_ref)
    return output_sources


def _column_id_ref(value: str) -> SourceRef | None:
    table_column = value.removeprefix("column:")
    parts = table_column.rsplit(".", 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _primary_upstream_table(statement: exp.Select) -> str | None:
    from_clause = statement.args.get("from_") or statement.args.get("from")
    if from_clause is None:
        return None
    table = from_clause.this
    if isinstance(table, exp.Table):
        return _table_identity(table)
    return None


def _relation_alias_map(statement: exp.Select) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    from_clause = statement.args.get("from_") or statement.args.get("from")
    if from_clause is not None:
        for table in from_clause.find_all(exp.Table):
            _register_table_alias(alias_map, table)
    for join in statement.args.get("joins") or []:
        join_table = join.this
        if isinstance(join_table, exp.Table):
            _register_table_alias(alias_map, join_table)
    return alias_map


def _register_table_alias(alias_map: dict[str, str], table: exp.Table) -> None:
    identity = _table_identity(table)
    alias = table.alias_or_name
    if alias and alias != identity.split(".")[-1]:
        alias_map[str(alias)] = identity
    alias_map[identity.split(".")[-1]] = identity
    alias_map[identity] = identity


def assert_lineage_truth_sql_oracle(
    *,
    case_root: Path,
    downstream_model: str,
    dialect: str,
    expected_edges: set[tuple[str, str]],
) -> None:
    """Fail when expected edges are not justified by compiled SQL value flow."""
    try:
        oracle_edges = _case_sql_oracle_edges(
            case_root,
            fallback_downstream_model=downstream_model,
            dialect=dialect,
        )
    except ValueError as exc:
        raise AssertionError(
            f"{case_root.name}: SQL passthrough oracle rejected compiled SQL: {exc}"
        ) from exc

    missing_from_sql = expected_edges - oracle_edges
    if missing_from_sql:
        raise AssertionError(
            f"{case_root.name}: expected edges not justified by SQL passthrough oracle: "
            f"{sorted(missing_from_sql)}"
        )


def _case_sql_oracle_edges(
    case_root: Path,
    *,
    fallback_downstream_model: str,
    dialect: str,
) -> set[tuple[str, str]]:
    manifest = case_root / "manifest.json"
    if not manifest.is_file():
        sql_path = case_root / "sql" / "model.sql"
        if not sql_path.is_file():
            raise AssertionError(
                f"{case_root.name}: lineage_truth case requires sql/model.sql or manifest.json"
            )
        return sql_cast_passthrough_oracle_edges(
            sql_path.read_text(encoding="utf-8"),
            downstream_model=fallback_downstream_model,
            dialect=dialect,
        )

    project = load_project(manifest, dialect=dialect)
    upstream_output_dependencies = _project_output_dependencies(
        project, dialect=dialect
    )
    edges: set[tuple[str, str]] = set()
    for dataset in project.datasets.values():
        if dataset.kind != "local" or not dataset.sql:
            continue
        edges.update(
            sql_cast_passthrough_oracle_edges(
                dataset.sql,
                downstream_model=dataset.name,
                dialect=dialect,
                upstream_output_dependencies=upstream_output_dependencies,
            )
        )
    return _normalize_oracle_edges_to_project(edges, project)


def _project_output_dependencies(
    project, *, dialect: str
) -> dict[SourceRef, set[SourceRef]]:
    dependencies: dict[SourceRef, set[SourceRef]] = {}
    for dataset in project.datasets.values():
        if dataset.kind != "local" or not dataset.sql:
            continue
        dependencies.update(
            _dataset_output_dependencies(
                dataset.name,
                dataset.sql,
                dialect=dialect,
            )
        )
    return dependencies


def _normalize_oracle_edges_to_project(
    edges: set[tuple[str, str]], project
) -> set[tuple[str, str]]:
    """Keep SQL oracle identities aligned with the fixture manifest."""
    by_leaf: dict[str, list[str]] = {}
    for name in project.datasets:
        by_leaf.setdefault(name.rsplit(".", 1)[-1], []).append(name)

    normalized: set[tuple[str, str]] = set()
    for downstream, upstream in edges:
        normalized.add(
            (
                _normalize_column_id_to_project(downstream, project, by_leaf),
                _normalize_column_id_to_project(upstream, project, by_leaf),
            )
        )
    return normalized


def _normalize_column_id_to_project(
    value: str, project, by_leaf: dict[str, list[str]]
) -> str:
    ref = _column_id_ref(value)
    if ref is None:
        return value
    table, column = ref
    if table in project.datasets or is_canonical_relation_id(table):
        return column_id(table, column)
    matches = by_leaf.get(table.rsplit(".", 1)[-1], [])
    if len(matches) == 1:
        return column_id(matches[0], column)
    if table.count(".") >= 2:
        return column_id(table, column)
    return column_id(table.rsplit(".", 1)[-1], column)


def _dataset_output_dependencies(
    dataset_name: str,
    sql: str,
    *,
    dialect: str,
) -> dict[SourceRef, set[SourceRef]]:
    statement = sqlglot.parse_one(sql, read=dialect)
    if not isinstance(statement, exp.Select):
        return {}
    dependencies: dict[SourceRef, set[SourceRef]] = {}
    for expression in statement.expressions:
        output_name = expression.alias_or_name
        if not output_name or output_name == "*":
            continue
        source_columns = {
            column.name
            for column in expression.find_all(exp.Column)
            if column.name and column.name != output_name
        }
        if source_columns:
            dependencies[(dataset_name, output_name)] = {
                (dataset_name, column_name) for column_name in source_columns
            }
    return dependencies


def _table_identity(table: exp.Table) -> str:
    return qualified_table_reference(table)


def _is_literal_projection(expression: Any) -> bool:
    if isinstance(expression, exp.Literal):
        return True
    if isinstance(expression, (exp.Cast, exp.TryCast)):
        return _is_literal_projection(expression.this)
    return False


def _passthrough_source(
    expression: Any,
    *,
    alias_to_table: dict[str, str],
    primary_upstream: str | None = None,
) -> tuple[str, str] | None:
    if isinstance(expression, (exp.Cast, exp.TryCast)):
        return _passthrough_source(
            expression.this,
            alias_to_table=alias_to_table,
            primary_upstream=primary_upstream,
        )
    if isinstance(expression, exp.Column):
        column_name = expression.name
        if not column_name:
            return None
        table_alias = expression.table
        if table_alias:
            upstream = alias_to_table.get(str(table_alias))
            if upstream is None:
                return None
            return upstream, column_name
        if primary_upstream is not None:
            return primary_upstream, column_name
        if len(set(alias_to_table.values())) == 1:
            upstream = next(iter(alias_to_table.values()))
            return upstream, column_name
        return None
    if isinstance(expression, exp.Alias):
        return _passthrough_source(
            expression.this,
            alias_to_table=alias_to_table,
            primary_upstream=primary_upstream,
        )
    return None


def _expression_sources(
    expression: Any,
    *,
    alias_to_table: dict[str, str],
    primary_upstream: str | None = None,
) -> set[SourceRef]:
    sources: set[SourceRef] = set()
    for column in expression.find_all(exp.Column):
        source = _passthrough_source(
            column,
            alias_to_table=alias_to_table,
            primary_upstream=primary_upstream,
        )
        if source is not None:
            sources.add(source)
    return sources
