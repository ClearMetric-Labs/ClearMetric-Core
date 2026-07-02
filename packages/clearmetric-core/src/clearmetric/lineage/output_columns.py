"""Pass A — infer model output columns from AST without sqlglot lineage()."""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot.expressions as exp
from clearmetric.core import normalize_identifier_part

from .errors import LineageInputError
from .loaders import ProjectDataset, ProjectInput
from .schema_registry import MissingSchema, SchemaRegistry, UnknownRelation
from .sql_analyzer import SqlStatementAnalysis, analyze_sql_statement


@dataclass(frozen=True)
class OutputColumnInference:
    column_names: tuple[str, ...]
    schema_missing: bool
    blocking_findings: tuple[str, ...]


def infer_output_columns(
    dataset: ProjectDataset,
    registry: SchemaRegistry,
    *,
    project: ProjectInput,
    dialect: str,
) -> OutputColumnInference:
    """Pass A: register inferred outputs; never copy partial upstream on missing."""
    if not dataset.sql:
        return OutputColumnInference((), False, ())

    try:
        analysis = analyze_sql_statement(dataset.sql, dialect=dialect)
    except LineageInputError:
        return OutputColumnInference((), True, ("parse_failed",))

    if analysis is None:
        return OutputColumnInference((), True, ("parse_failed",))

    names, findings = _output_names_from_select(
        analysis,
        registry=registry,
        project=project,
        dialect=dialect,
    )
    column_types = {
        name: dataset.column_types[name]
        for name in names
        if name in dataset.column_types
    }
    if names:
        registry.register_inferred_outputs(
            dataset.name,
            column_names=names,
            column_types=column_types or None,
            schema_source="inferred",
        )
    schema_missing = bool(findings)
    return OutputColumnInference(
        column_names=names,
        schema_missing=schema_missing,
        blocking_findings=tuple(sorted(set(findings))),
    )


def _output_names_from_select(
    analysis: SqlStatementAnalysis,
    *,
    registry: SchemaRegistry,
    project: ProjectInput,
    dialect: str,
) -> tuple[tuple[str, ...], list[str]]:
    del dialect
    findings: list[str] = []
    names: list[str] = []

    for select in analysis.statement.find_all(exp.Select):
        for expression in select.expressions:
            if isinstance(expression, exp.Star):
                expanded, star_findings = _expand_star(
                    None,
                    analysis=analysis,
                    registry=registry,
                    project=project,
                )
                names.extend(expanded)
                findings.extend(star_findings)
                continue
            if isinstance(expression, exp.Column) and isinstance(
                expression.this, exp.Star
            ):
                table_ref = expression.table
                expanded, star_findings = _expand_star(
                    str(table_ref) if table_ref else None,
                    analysis=analysis,
                    registry=registry,
                    project=project,
                )
                names.extend(expanded)
                findings.extend(star_findings)
                continue
            alias = expression.alias_or_name
            if alias and alias != "*":
                try:
                    names.append(normalize_identifier_part(alias))
                except Exception:
                    continue

    deduped = tuple(sorted(set(names)))
    return deduped, findings


def _expand_star(
    table_ref: str | None,
    *,
    analysis: SqlStatementAnalysis,
    registry: SchemaRegistry,
    project: ProjectInput,
) -> tuple[list[str], list[str]]:
    """Expand SELECT * — invariant: never partial copy on MissingSchema/UnknownRelation."""
    findings: list[str] = []
    if table_ref is None:
        from .sql_analyzer import from_clause_base_relations

        relations = from_clause_base_relations(analysis)
        if len(relations) != 1:
            findings.append("partial_star_expansion")
            return [], findings
        table_ref = relations[0]

    resolved_ref = analysis.alias_map.get(table_ref, table_ref)
    upstream = registry.resolve_relation(
        resolved_ref,
        alias_map=analysis.alias_map,
    )

    if isinstance(upstream, UnknownRelation):
        findings.append("identity_unresolved")
        return [], findings

    if isinstance(upstream, MissingSchema):
        findings.append("schema_missing")
        return [], findings

    return list(upstream.column_names), findings
