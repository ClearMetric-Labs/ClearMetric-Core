"""Union safety and per-column lineage edge resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from clearmetric.core import normalize_identifier, normalize_identifier_part
from sqlglot import exp
from sqlglot.lineage import Node as SqlglotLineageNode

from .errors import LineageInputError
from .loaders import ProjectDataset, ProjectInput
from .refs import (
    collect_all_refs,
    collect_immediate_upstream_refs,
    collect_leaf_refs,
    is_local_ref,
    refs_confined_to_qualified_star_aliases,
    refs_from_lineage_subtree,
    refs_target_only_root_datasets,
    remap_root_sources_to_local_deps,
    try_split_ref,
)
from .relations import normalize_relation_id, relation_fqn_lookup_keys, resolve_sql_visible_table_ref
from .schema_registry import MissingSchema, RelationSchema, SchemaRegistry
from .sql_parse import (
    SqlStatementAnalysis,
    cte_select_body,
    from_clause_base_relations,
    outer_select,
    select_expression_for_output,
)
from .sql_analyzer import (
    StarExpansionPolicy,
    bare_star_column_upstream,
    cte_output_uses_secondary_join_source,
    cte_projected_column_is_literal,
    cte_single_source_base_relation,
    expression_column_refs,
    filter_value_lineage_refs,
    is_macro_generated_union,
    is_star_suppressed_output,
    lineage_output_map,
    macro_union_branch_base_relations,
    macro_union_schema_branch_refs_index,
    mixed_explicit_and_star_outer_select,
    nested_case_expressions,
    outer_union_cte_first_branch_refs,
    projection_source_column_names,
    resolve_star_suppressed_column_upstream,
    shadowed_outer_select_aliases,
    single_bare_star_source_relation,
    star_suppressed_cte_column_resolves_outside_dependencies,
    union_branch_upstream_refs,
    union_has_null_padding_asymmetry,
    unwrap_alias,
)

RefSelectionStrategy = Literal[
    "immediate",
    "union_branches",
    "local_refs",
    "leaf_refs",
    "expanded_star",
    "skipped_union",
    "skipped_star_suppressed",
    "skipped_quoted",
    "skipped_star_output",
    "no_refs",
]


@dataclass(frozen=True)
class ColumnLineageResolution:
    output_column: str
    ref_selection_strategy: RefSelectionStrategy
    pre_filter_refs: frozenset[str]
    post_filter_refs: frozenset[str]
    mapped_edges: frozenset[tuple[str, str, str, str]]
    star_suppressed: bool
    warning_code: str | None
    cte_chain: tuple[str, ...] = ()
    cte_local_aliases: frozenset[tuple[str, str]] = frozenset()
    warning_message: str | None = None


__all__ = [
    "ColumnLineageResolution",
    "RefSelectionStrategy",
    "build_unqualified_lineage_map",
    "macro_union_schema_refs_index_for_dataset",
    "merge_macro_union_output_columns",
    "registry_proven_star_resolution",
    "resolve_output_column_lineage",
]


def build_unqualified_lineage_map(
    sql: str,
    *,
    schema: dict[str, dict[str, str]],
    dialect: str,
) -> dict[str, SqlglotLineageNode]:
    """Fetch unqualified lineage map (sources=None) for union branch analysis."""
    return lineage_output_map(sql, schema=schema, sources=None, dialect=dialect)


def merge_macro_union_output_columns(
    output_map: dict[str, SqlglotLineageNode],
    *,
    unqualified_lineage_map: dict[str, SqlglotLineageNode],
) -> None:
    """Merge macro-union output columns that appear only in the unqualified lineage map."""
    qualified_keys = {
        normalize_identifier_part(name) for name in output_map if name != "*"
    }
    for column, unqualified_root in unqualified_lineage_map.items():
        if column == "*" or column in output_map:
            continue
        if normalize_identifier_part(column) in qualified_keys:
            continue
        output_map[column] = unqualified_root


def macro_union_schema_refs_index_for_dataset(
    *,
    dataset: ProjectDataset,
    project: ProjectInput,
    analysis: SqlStatementAnalysis,
    registry: SchemaRegistry,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str],
    cte_names: set[str] | frozenset[str],
) -> dict[str, set[str]] | None:
    """Build macro-union branch ref index when the statement is a macro-generated union."""
    if not is_macro_generated_union(analysis):
        return None
    allowed = union_allowed_parent_ids(
        dataset,
        table_references=table_references,
        project=project,
    )

    def resolve_canonical_parent(base_relation: str) -> str | None:
        return canonical_upstream_relation_id(
            base_relation,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_names,
            registry=registry,
        )

    for base_relation in macro_union_branch_base_relations(analysis):
        canonical = resolve_canonical_parent(base_relation)
        for candidate in (canonical, base_relation):
            if not candidate:
                continue
            normalized = normalize_identifier(candidate)
            allowed.add(normalized)
            allowed.update(relation_fqn_lookup_keys(normalized))

    return macro_union_schema_branch_refs_index(
        analysis=analysis,
        registry=registry,
        project=project,
        resolve_canonical_parent=resolve_canonical_parent,
        parent_is_allowed=lambda parent: parent_allowed_for_union(parent, allowed),
    )


def parent_is_resolvable(
    parent_name: str,
    *,
    project: ProjectInput,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str] = (),
    cte_names: set[str] | frozenset[str] = frozenset(),
    registry: SchemaRegistry | None = None,
) -> bool:
    if registry is None:
        return False
    canonical = canonical_upstream_relation_id(
        parent_name,
        project=project,
        alias_map=alias_map,
        table_references=table_references,
        cte_names=cte_names,
        registry=registry,
    )
    return canonical is not None


def extract_cte_context(
    refs: set[str],
    *,
    cte_names: set[str],
    alias_map: dict[str, str],
) -> tuple[tuple[str, ...], frozenset[tuple[str, str]]]:
    chain: list[str] = []
    aliases: set[tuple[str, str]] = set()
    for ref in refs:
        parsed = try_split_ref(ref)
        if parsed is None:
            continue
        parent_name, _column_name = parsed
        parent_key = normalize_identifier_part(parent_name)
        if parent_key in cte_names:
            chain.append(parent_key)
        if parent_key in alias_map:
            aliases.add((parent_key, alias_map[parent_key]))
    return tuple(dict.fromkeys(chain)), frozenset(aliases)


def registry_proven_star_resolution(
    *,
    output_name: str,
    dataset: ProjectDataset,
    project: ProjectInput,
    lineage_schema: dict[str, dict[str, str]],
    statement_analysis,
    registry: SchemaRegistry,
    pre_filter_refs: frozenset[str] = frozenset(),
    post_filter_refs: frozenset[str] = frozenset(),
    cte_chain: tuple[str, ...] = (),
    cte_local_aliases: frozenset[tuple[str, str]] = frozenset(),
) -> ColumnLineageResolution | None:
    registry_upstream = bare_star_column_upstream(
        output_name,
        analysis=statement_analysis,
        schema=lineage_schema,
        datasets=project.datasets,
        registry=registry,
    )
    if registry_upstream is None:
        return None
    parent_id, source_column = registry_upstream
    return ColumnLineageResolution(
        output_column=output_name,
        ref_selection_strategy="expanded_star",
        pre_filter_refs=pre_filter_refs,
        post_filter_refs=post_filter_refs,
        mapped_edges=frozenset({(parent_id, source_column, dataset.name, output_name)}),
        star_suppressed=False,
        warning_code=None,
        cte_chain=cte_chain,
        cte_local_aliases=cte_local_aliases,
    )


def refs_target_outer_base_relations(
    refs: set[str],
    *,
    statement_analysis,
    alias_map: dict[str, str],
) -> bool:
    if not refs:
        return False
    allowed = set(from_clause_base_relations(statement_analysis))
    for alias, target in alias_map.items():
        target_leaf = normalize_identifier_part(target.split(".")[-1])
        if target in allowed or target_leaf in allowed:
            allowed.add(alias)
            allowed.add(target)
    for ref in refs:
        if ref == "*":
            continue
        parsed = try_split_ref(ref)
        if parsed is None:
            return False
        parent_name, _column_name = parsed
        parent_key = normalize_identifier_part(parent_name)
        resolved = alias_map.get(parent_key, parent_key)
        resolved_leaf = normalize_identifier_part(resolved.split(".")[-1])
        if (
            parent_key not in allowed
            and resolved not in allowed
            and resolved_leaf not in allowed
        ):
            return False
    return True


def refs_resolve_to_project_datasets(
    refs: set[str],
    *,
    project: ProjectInput,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str],
    cte_names: set[str] | frozenset[str],
    registry: SchemaRegistry,
) -> bool:
    """True when every ref parent resolves to a concrete project dataset (union-safe)."""
    if not refs or refs == {"*"}:
        return False
    for ref in refs:
        if ref == "*":
            return False
        parsed = try_split_ref(ref)
        if parsed is None:
            return False
        parent_name, _column_name = parsed
        try:
            resolved = resolve_sql_visible_table_ref(
                parent_name,
                project=project,
                alias_map=alias_map,
                table_references=table_references,
                cte_names=cte_names,
            )
        except LineageInputError:
            return False
        if resolved not in project.datasets:
            return False
    return True


def outer_from_is_cte_only(statement_analysis) -> bool:
    """True when the outer FROM/JOIN lists only CTE relations (no base tables)."""
    relations = from_clause_base_relations(statement_analysis)
    if not relations:
        return False
    return all(relation in statement_analysis.cte_names for relation in relations)


def canonical_upstream_relation_id(
    parent_name: str,
    *,
    project: ProjectInput,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str],
    cte_names: set[str] | frozenset[str],
    registry: SchemaRegistry,
) -> str | None:
    try:
        resolved = resolve_sql_visible_table_ref(
            parent_name,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_names,
        )
    except LineageInputError:
        return None
    outcome = registry.resolve_relation(resolved, alias_map=alias_map)
    if isinstance(outcome, (RelationSchema, MissingSchema)):
        return outcome.relation_id
    resolved_key = normalize_identifier_part(resolved)
    if resolved in cte_names or resolved_key in cte_names:
        dataset_key = normalize_identifier(resolved)
        if dataset_key in project.datasets:
            return dataset_key
        return None

    parent_key = normalize_identifier_part(parent_name)
    normalized_refs = {
        normalize_identifier(reference) for reference in table_references
    }
    if (
        parent_key in alias_map
        and normalize_identifier(resolved) == normalize_identifier(parent_key)
        and parent_key not in cte_names
        and normalize_identifier(parent_key) not in normalized_refs
    ):
        return None

    if resolved in project.datasets:
        return normalize_identifier(resolved)

    resolved_norm = normalize_identifier(resolved)
    if resolved_norm in normalized_refs:
        return resolved_norm
    from .relations import relation_fqn_lookup_keys

    if relation_fqn_lookup_keys(resolved_norm) & normalized_refs:
        return resolved_norm
    return None


def resolve_ref_to_mapped_edges(
    leaf_ref: str,
    *,
    output_name: str,
    dataset_name: str,
    statement_analysis: SqlStatementAnalysis | None,
    project: ProjectInput,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str],
    cte_names: set[str] | frozenset[str],
    registry: SchemaRegistry,
) -> set[tuple[str, str, str, str]]:
    """Expand a qualified ref into concrete upstream edges, walking CTE projections."""
    parsed = try_split_ref(leaf_ref)
    if parsed is None:
        return set()
    parent_name, source_column = parsed
    parent_key = normalize_identifier_part(parent_name)
    if (
        parent_key in cte_names
        and statement_analysis is not None
    ):
        cte_select = cte_select_body(parent_key, analysis=statement_analysis)
        if cte_select is not None:
            expression = select_expression_for_output(cte_select, source_column)
            if expression is not None:
                mapped: set[tuple[str, str, str, str]] = set()
                for relation, column in expression_column_refs(
                    unwrap_alias(expression),
                    analysis=statement_analysis,
                    select=cte_select,
                ):
                    canonical = canonical_upstream_relation_id(
                        relation,
                        project=project,
                        alias_map=alias_map,
                        table_references=table_references,
                        cte_names=cte_names,
                        registry=registry,
                    )
                    if canonical is None:
                        continue
                    canonical_key = normalize_identifier_part(canonical)
                    if (
                        canonical_key in cte_names
                        and canonical not in project.datasets
                    ):
                        continue
                    mapped.add(
                        (canonical, column, dataset_name, output_name),
                    )
                if mapped:
                    return mapped
    canonical = canonical_upstream_relation_id(
        parent_name,
        project=project,
        alias_map=alias_map,
        table_references=table_references,
        cte_names=cte_names,
        registry=registry,
    )
    if canonical is None:
        return set()
    canonical_key = normalize_identifier_part(canonical)
    if canonical_key in cte_names and canonical not in project.datasets:
        return set()
    return {(canonical, source_column, dataset_name, output_name)}


def resolved_ref_parent_names(
    refs: set[str],
    *,
    project: ProjectInput,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str],
    cte_names: set[str] | frozenset[str],
    registry: SchemaRegistry,
) -> set[str]:
    parents: set[str] = set()
    for ref in refs:
        if ref == "*":
            continue
        parsed = try_split_ref(ref)
        if parsed is None:
            continue
        parent_name, _column_name = parsed
        canonical = canonical_upstream_relation_id(
            parent_name,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_names,
            registry=registry,
        )
        if canonical is not None:
            parents.add(canonical)
    return parents


def dependency_closure_relation_names(
    dataset: ProjectDataset,
    project: ProjectInput,
) -> set[str]:
    from .relations import relation_fqn_lookup_keys

    closure: set[str] = set()
    stack = list(dataset.dependency_names)
    while stack:
        name = stack.pop()
        if name in closure:
            continue
        closure.add(name)
        closure.add(normalize_identifier(name))
        closure.update(relation_fqn_lookup_keys(name))
        dependency = project.datasets.get(name)
        if dependency is not None:
            stack.extend(dependency.dependency_names)
    return closure


def union_allowed_parent_ids(
    dataset: ProjectDataset,
    *,
    table_references: tuple[str, ...] | frozenset[str],
    project: ProjectInput,
) -> set[str]:
    from .relations import normalize_relation_id, relation_fqn_lookup_keys

    allowed: set[str] = set()
    for dep in dataset.dependency_names:
        allowed.add(normalize_identifier(dep))
        allowed.update(relation_fqn_lookup_keys(dep))
    for reference in table_references:
        normalized = normalize_identifier(reference)
        allowed.add(normalized)
        allowed.update(relation_fqn_lookup_keys(normalized))
        try:
            allowed.add(
                normalize_relation_id(
                    normalized,
                    project=project,
                    alias_map=None,
                )
            )
        except LineageInputError:
            pass
    return allowed


def parent_allowed_for_union(parent_id: str, allowed_ids: set[str]) -> bool:
    from .relations import relation_fqn_lookup_keys

    keys = {normalize_identifier(parent_id)}
    keys.update(relation_fqn_lookup_keys(parent_id))
    return bool(keys & allowed_ids)


def macro_union_branch_parents_safe(
    resolved_parents: set[str],
    *,
    allowed: set[str],
    project: ProjectInput,
) -> bool:
    """Allow multi-branch unions when every branch resolves to an allowed project dataset."""
    if len(resolved_parents) < 2:
        return False
    for parent in resolved_parents:
        if parent not in project.datasets:
            return False
        if not parent_allowed_for_union(parent, allowed):
            return False
    return True


def union_refs_are_safe_to_resolve(
    refs: set[str],
    *,
    statement_analysis,
    project: ProjectInput,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str],
    cte_names: set[str] | frozenset[str],
    registry: SchemaRegistry,
    dataset: ProjectDataset,
) -> bool:
    if refs_target_outer_base_relations(
        refs,
        statement_analysis=statement_analysis,
        alias_map=alias_map,
    ):
        return True
    allowed = union_allowed_parent_ids(
        dataset,
        table_references=table_references,
        project=project,
    )
    resolved_parents = resolved_ref_parent_names(
        refs,
        project=project,
        alias_map=alias_map,
        table_references=table_references,
        cte_names=cte_names,
        registry=registry,
    )
    if isinstance(statement_analysis.statement, exp.Union):
        if (
            is_macro_generated_union(statement_analysis)
            and resolved_parents
            and all(
                parent in project.datasets
                and parent_allowed_for_union(parent, allowed)
                for parent in resolved_parents
            )
        ):
            return True
        if union_has_null_padding_asymmetry(statement_analysis) and resolved_parents and all(
            parent in project.datasets
            and parent_allowed_for_union(parent, allowed)
            for parent in resolved_parents
        ):
            return True
    if not isinstance(statement_analysis.statement, exp.Union) and macro_union_branch_parents_safe(
        resolved_parents,
        allowed=allowed,
        project=project,
    ):
        return True
    if not outer_from_is_cte_only(statement_analysis):
        return False
    if not refs_resolve_to_project_datasets(
        refs,
        project=project,
        alias_map=alias_map,
        table_references=table_references,
        cte_names=cte_names,
        registry=registry,
    ):
        return False
    if not resolved_parents:
        return False
    for parent in resolved_parents:
        if parent not in project.datasets:
            return False
        if not parent_allowed_for_union(parent, allowed):
            return False
    return True


def expand_union_branch_cte_refs(
    refs: set[str],
    *,
    statement_analysis,
    project: ProjectInput,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str],
    cte_names: set[str] | frozenset[str],
    registry: SchemaRegistry,
) -> set[str]:
    expanded: set[str] = set()
    for ref in refs:
        parsed = try_split_ref(ref)
        if parsed is None:
            continue
        parent_name, column_name = parsed
        parent_key = normalize_identifier_part(parent_name)
        if parent_key not in cte_names:
            expanded.add(normalize_identifier(f"{parent_key}.{column_name}"))
            continue
        base_relation = cte_single_source_base_relation(
            parent_key,
            analysis=statement_analysis,
        )
        if base_relation is None:
            expanded.add(normalize_identifier(f"{parent_key}.{column_name}"))
            continue
        canonical = canonical_upstream_relation_id(
            base_relation,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_names,
            registry=registry,
        )
        if canonical is None:
            expanded.add(normalize_identifier(f"{parent_key}.{column_name}"))
            continue
        expanded.add(normalize_identifier(f"{canonical}.{column_name}"))
    return expanded


def resolvable_union_branch_refs(
    refs: set[str],
    *,
    statement_analysis,
    project: ProjectInput,
    dataset: ProjectDataset,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str],
    cte_names: set[str] | frozenset[str],
    registry: SchemaRegistry,
) -> set[str]:
    expanded = expand_union_branch_cte_refs(
        refs,
        statement_analysis=statement_analysis,
        project=project,
        alias_map=alias_map,
        table_references=table_references,
        cte_names=cte_names,
        registry=registry,
    )
    allowed = union_allowed_parent_ids(
        dataset,
        table_references=table_references,
        project=project,
    )
    resolvable: set[str] = set()
    for ref in expanded:
        parsed = try_split_ref(ref)
        if parsed is None:
            continue
        parent_name, column_name = parsed
        canonical = canonical_upstream_relation_id(
            parent_name,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_names,
            registry=registry,
        )
        if canonical is None or canonical not in project.datasets:
            continue
        if not parent_allowed_for_union(canonical, allowed):
            continue
        resolvable.add(normalize_identifier(f"{canonical}.{column_name}"))
    return resolvable


def union_output_prefers_first_branch_only(
    output_name: str,
    *,
    analysis: SqlStatementAnalysis | None,
) -> bool:
    """True when union sibling refs should collapse to the first branch only."""
    if analysis is None:
        return True
    outer = outer_select(analysis.statement)
    if not isinstance(outer, exp.Select):
        return True
    expression = select_expression_for_output(outer, output_name)
    if expression is None:
        return True
    unwrapped = unwrap_alias(expression)
    if isinstance(unwrapped, exp.Column):
        return normalize_identifier_part(unwrapped.name or "") != normalize_identifier_part(
            output_name
        )
    return True


def collect_union_branch_upstream_refs(
    *,
    output_name: str,
    dataset: ProjectDataset,
    project: ProjectInput,
    alias_map: dict[str, str],
    cte_names: set[str] | frozenset[str],
    known_relation_names: set[str],
    lineage_schema: dict[str, dict[str, str]],
    dialect: str,
    statement_analysis,
    table_references: tuple[str, ...] | frozenset[str],
    registry: SchemaRegistry,
    unqualified_lineage_map: dict[str, SqlglotLineageNode] | None = None,
    macro_union_schema_refs_index: dict[str, set[str]] | None = None,
) -> set[str]:
    sqlglot_branch_refs = collect_union_branch_refs(
        output_name=output_name,
        dataset=dataset,
        project=project,
        alias_map=alias_map,
        cte_names=cte_names,
        known_relation_names=known_relation_names,
        lineage_schema=lineage_schema,
        dialect=dialect,
        unqualified_lineage_map=unqualified_lineage_map,
        statement_analysis=statement_analysis,
    )
    if (
        statement_analysis is not None
        and len(sqlglot_branch_refs) > 1
        and union_output_prefers_first_branch_only(
            output_name,
            analysis=statement_analysis,
        )
    ):
        parent_aliases: set[str] = set()
        for ref in sqlglot_branch_refs:
            parsed = try_split_ref(ref)
            if parsed is not None:
                parent_aliases.add(normalize_identifier_part(parsed[0]))
        if len(parent_aliases) > 1:
            first_branch_refs = outer_union_cte_first_branch_refs(
                output_name,
                analysis=statement_analysis,
            )
            if first_branch_refs:
                sqlglot_branch_refs = first_branch_refs

    def resolve_canonical_parent(base_relation: str) -> str | None:
        return canonical_upstream_relation_id(
            base_relation,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_names,
            registry=registry,
        )

    allowed = union_allowed_parent_ids(
        dataset,
        table_references=table_references,
        project=project,
    )

    return union_branch_upstream_refs(
        output_name,
        analysis=statement_analysis,
        sqlglot_branch_refs=sqlglot_branch_refs,
        registry=registry,
        project=project,
        resolve_canonical_parent=resolve_canonical_parent,
        parent_is_allowed=lambda parent: parent_allowed_for_union(parent, allowed),
        macro_union_schema_refs_index=macro_union_schema_refs_index,
    )


def lineage_root_for_output(
    lineage_map: dict[str, SqlglotLineageNode],
    output_name: str,
) -> SqlglotLineageNode | None:
    """Return a lineage root keyed by exact or normalized output column name."""
    if output_name in lineage_map:
        return lineage_map[output_name]
    target = normalize_identifier_part(output_name)
    for key, root in lineage_map.items():
        if normalize_identifier_part(key) == target:
            return root
    return None


def collect_union_branch_refs(
    *,
    output_name: str,
    dataset: ProjectDataset,
    project: ProjectInput,
    alias_map: dict[str, str],
    cte_names: set[str] | frozenset[str],
    known_relation_names: set[str],
    lineage_schema: dict[str, dict[str, str]],
    dialect: str,
    unqualified_lineage_map: dict[str, SqlglotLineageNode] | None = None,
    statement_analysis: SqlStatementAnalysis | None = None,
) -> set[str]:
    """Collect per-branch upstream refs for UNION outputs using unqualified lineage."""
    sql = dataset.sql
    if not sql:
        return set()
    if unqualified_lineage_map is None:
        return set()
    unqualified_root = lineage_root_for_output(
        unqualified_lineage_map,
        output_name,
    )
    if unqualified_root is None:
        return set()
    branch_sets: list[set[str]] = []
    for child in unqualified_root.downstream:
        branch_sets.append(
            refs_from_lineage_subtree(
                child,
                project=project,
                dataset=dataset,
                alias_map=alias_map,
                cte_names=set(cte_names),
                schema=lineage_schema,
                preserve_cte_scope=True,
            )
        )
    if len(branch_sets) > 1 and all(branch_sets):
        if statement_analysis is not None and not union_output_prefers_first_branch_only(
            output_name,
            analysis=statement_analysis,
        ):
            union_refs = set()
            for refs in branch_sets:
                union_refs.update(refs)
        else:
            union_refs = branch_sets[0]
    else:
        union_refs = set()
        for refs in branch_sets:
            union_refs.update(refs)
    return remap_root_sources_to_local_deps(
        union_refs,
        project=project,
        dataset=dataset,
        known_relation_names=known_relation_names,
        schema=lineage_schema,
    )


def restrict_mapped_edges_for_outer_union_first_branch(
    mapped: set[tuple[str, str, str, str]],
    *,
    output_name: str,
    root: SqlglotLineageNode,
    analysis: SqlStatementAnalysis,
    project: ProjectInput,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str],
    cte_names: set[str] | frozenset[str],
    registry: SchemaRegistry,
) -> set[tuple[str, str, str, str]]:
    """Prefer outer UNION CTE first-branch upstreams when siblings disagree."""
    if len({edge[0] for edge in mapped}) <= 1:
        return mapped
    if all(edge[1] == edge[3] for edge in mapped):
        return mapped
    first_parents: set[str] = set()
    first_refs = outer_union_cte_first_branch_refs(output_name, analysis=analysis)
    if first_refs:
        expanded = expand_union_branch_cte_refs(
            first_refs,
            statement_analysis=analysis,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_names,
            registry=registry,
        )
        for ref in expanded:
            parsed = try_split_ref(ref)
            if parsed is None:
                continue
            parent_name, _column_name = parsed
            canonical = canonical_upstream_relation_id(
                parent_name,
                project=project,
                alias_map=alias_map,
                table_references=table_references,
                cte_names=cte_names,
                registry=registry,
            )
            if canonical is not None:
                first_parents.add(canonical)
    if not first_parents:
        outer = outer_select(analysis.statement)
        if isinstance(outer, exp.Select):
            from_clause = outer.args.get("from_") or outer.args.get("from")
            if from_clause is not None:
                tables = [
                    table
                    for table in from_clause.find_all(exp.Table)
                    if isinstance(table, exp.Table)
                ]
                if len(tables) == 1:
                    cte_name = normalize_identifier_part(tables[0].name or "")
                    if cte_name in analysis.cte_names:
                        base = cte_single_source_base_relation(
                            cte_name,
                            analysis=analysis,
                        )
                        if base is not None:
                            canonical = canonical_upstream_relation_id(
                                base,
                                project=project,
                                alias_map=alias_map,
                                table_references=table_references,
                                cte_names=cte_names,
                                registry=registry,
                            )
                            if canonical is not None:
                                first_parents.add(canonical)
    if len(first_parents) != 1:
        return mapped
    outer = outer_select(analysis.statement)
    defining_expression = (
        select_expression_for_output(outer, output_name)
        if isinstance(outer, exp.Select)
        else None
    )
    if defining_expression is None:
        defining_expression = root.expression
    if defining_expression is not None:
        unwrapped = unwrap_alias(defining_expression)
        if isinstance(unwrapped, exp.Column):
            table = unwrapped.args.get("table")
            if isinstance(table, exp.Identifier):
                relation_name = normalize_identifier_part(table.this)
                cte_name = relation_name
                if cte_name not in analysis.cte_names:
                    mapped_relation = alias_map.get(relation_name, relation_name)
                    cte_name = normalize_identifier_part(mapped_relation)
                if cte_name in analysis.cte_names:
                    cte_select = cte_select_body(cte_name, analysis=analysis)
                    column_name = normalize_identifier_part(unwrapped.name or output_name)
                    if cte_select is not None:
                        cte_expression = select_expression_for_output(
                            cte_select,
                            column_name,
                        )
                        if cte_expression is not None and not isinstance(
                            unwrap_alias(cte_expression),
                            exp.Column,
                        ):
                            return mapped
        if not isinstance(unwrapped, exp.Column):
            return mapped
        if nested_case_expressions(unwrapped):
            return mapped
    return {edge for edge in mapped if edge[0] in first_parents}


def derived_audit_column_supplement(
    selected_refs: set[str],
    *,
    root: SqlglotLineageNode,
    analysis: SqlStatementAnalysis,
) -> set[str]:
    """Add expression leaf columns alongside passthrough union-branch refs."""
    if root.expression is None:
        return selected_refs
    expression = unwrap_alias(root.expression)
    if isinstance(expression, exp.Column):
        return selected_refs
    outer = outer_select(analysis.statement)
    leaf_columns: set[str] = set()
    for _relation, name in expression_column_refs(
        expression,
        analysis=analysis,
        select=outer if isinstance(outer, exp.Select) else None,
    ):
        direct = normalize_identifier_part(name)
        projected = projection_source_column_names(name, analysis=analysis)
        if projected:
            leaf_columns.update(projected)
        else:
            leaf_columns.add(direct)
    if not leaf_columns:
        return selected_refs
    supplemented = set(selected_refs)
    for ref in selected_refs:
        parsed = try_split_ref(ref)
        if parsed is None:
            continue
        parent, column = parsed
        column_key = normalize_identifier_part(column)
        for leaf_column in leaf_columns:
            if leaf_column != column_key:
                supplemented.add(normalize_identifier(f"{parent}.{leaf_column}"))
    return supplemented


def resolve_output_column_lineage(
    *,
    output_name: str,
    root: SqlglotLineageNode,
    dataset: ProjectDataset,
    project: ProjectInput,
    dialect: str,
    lineage_schema: dict[str, dict[str, str]],
    alias_map: dict[str, str],
    cte_name_set: set[str],
    table_references: tuple[str, ...] | frozenset[str] = (),
    known_relation_names: set[str],
    has_union: bool,
    has_select_star: bool,
    star_policy: StarExpansionPolicy | None,
    quoted_outputs: frozenset[str],
    aliased_table_star: bool,
    statement_analysis,
    registry: SchemaRegistry,
    unqualified_lineage_map: dict[str, SqlglotLineageNode] | None = None,
    macro_union_schema_refs_index: dict[str, set[str]] | None = None,
) -> ColumnLineageResolution:
    normalized_output = normalize_identifier_part(output_name)
    empty = frozenset[tuple[str, str, str, str]]()
    shadowed_aliases = (
        shadowed_outer_select_aliases(statement_analysis)
        if statement_analysis is not None
        else {}
    )
    if has_select_star and is_star_suppressed_output(
        output_name,
        star_policy,
        statement_analysis=statement_analysis,
        lineage_root=root,
    ):
        allowed_relations = dependency_closure_relation_names(dataset, project)
        star_upstream = resolve_star_suppressed_column_upstream(
            output_name,
            analysis=statement_analysis,
            root=root,
            registry=registry,
            schema=lineage_schema,
            datasets=project.datasets,
            allowed_relations=allowed_relations,
        )
        if star_upstream is not None:
            return ColumnLineageResolution(
                output_column=output_name,
                ref_selection_strategy=(
                    "expanded_star"
                    if not star_upstream.via_cte_passthrough
                    else "immediate"
                ),
                pre_filter_refs=frozenset(),
                post_filter_refs=frozenset(),
                mapped_edges=frozenset(
                    {
                        (
                            star_upstream.parent_id,
                            star_upstream.source_column,
                            dataset.name,
                            output_name,
                        )
                    }
                ),
                star_suppressed=False,
                warning_code=None,
            )
        outer_star_cte = (
            single_bare_star_source_relation(statement_analysis)
            if statement_analysis is not None
            else None
        )
        allow_fallthrough = (
            statement_analysis is not None
            and mixed_explicit_and_star_outer_select(statement_analysis)
            and outer_star_cte is not None
            and (
                star_suppressed_cte_column_resolves_outside_dependencies(
                    output_name,
                    root,
                    statement_analysis=statement_analysis,
                    allowed_relations=allowed_relations,
                )
                or cte_output_uses_secondary_join_source(
                    outer_star_cte,
                    output_name,
                    analysis=statement_analysis,
                )
            )
        )
        if not allow_fallthrough:
            return ColumnLineageResolution(
                output_column=output_name,
                ref_selection_strategy="skipped_star_suppressed",
                pre_filter_refs=frozenset(),
                post_filter_refs=frozenset(),
                mapped_edges=empty,
                star_suppressed=True,
                warning_code=None,
            )
    if normalized_output in quoted_outputs:
        return ColumnLineageResolution(
            output_column=output_name,
            ref_selection_strategy="skipped_quoted",
            pre_filter_refs=frozenset(),
            post_filter_refs=frozenset(),
            mapped_edges=empty,
            star_suppressed=False,
            warning_code="unresolved_lineage",
        )

    ref_strategy: RefSelectionStrategy = "no_refs"
    selected_refs: set[str] = set()
    union_branch_refs: set[str] = set()
    resolvable_union: set[str] = set()
    if has_union and unqualified_lineage_map is None:
        return ColumnLineageResolution(
            output_column=output_name,
            ref_selection_strategy="skipped_union",
            pre_filter_refs=frozenset(),
            post_filter_refs=frozenset(),
            mapped_edges=empty,
            star_suppressed=False,
            warning_code="lineage_resolution_failed",
        )
    if has_union:
        union_branch_refs = collect_union_branch_upstream_refs(
            output_name=output_name,
            dataset=dataset,
            project=project,
            alias_map=alias_map,
            cte_names=cte_name_set,
            known_relation_names=known_relation_names,
            lineage_schema=lineage_schema,
            dialect=dialect,
            statement_analysis=statement_analysis,
            table_references=table_references,
            registry=registry,
            unqualified_lineage_map=unqualified_lineage_map,
            macro_union_schema_refs_index=macro_union_schema_refs_index,
        )
    immediate_refs = collect_immediate_upstream_refs(
        root,
        project=project,
        dataset=dataset,
        alias_map=alias_map,
        shadowed_aliases=shadowed_aliases,
        cte_names=cte_name_set,
        known_relation_names=known_relation_names,
        schema=lineage_schema,
    )
    if union_branch_refs:
        resolvable_union = resolvable_union_branch_refs(
            union_branch_refs,
            statement_analysis=statement_analysis,
            project=project,
            dataset=dataset,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_name_set,
            registry=registry,
        )
        if resolvable_union and union_refs_are_safe_to_resolve(
            resolvable_union,
            statement_analysis=statement_analysis,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_name_set,
            registry=registry,
            dataset=dataset,
        ):
            selected_refs = resolvable_union
            ref_strategy = "union_branches"
    null_padded_union = (
        has_union
        and statement_analysis is not None
        and union_has_null_padding_asymmetry(statement_analysis)
    )
    if null_padded_union and not selected_refs:
        return ColumnLineageResolution(
            output_column=output_name,
            ref_selection_strategy="skipped_union",
            pre_filter_refs=frozenset(union_branch_refs),
            post_filter_refs=frozenset(),
            mapped_edges=empty,
            star_suppressed=False,
            warning_code=None,
        )
    union_blocked = bool(resolvable_union) or null_padded_union
    if not selected_refs and immediate_refs and not union_blocked:
        selected_refs = immediate_refs
        ref_strategy = "immediate"
    elif not selected_refs and not union_blocked:
        all_refs = {
            ref
            for ref in collect_all_refs(root)
            if ref != output_name and ref != f"{dataset.name}.{output_name}"
        }
        local_refs = {
            ref
            for ref in all_refs
            if is_local_ref(ref, project=project, current_dataset=dataset.name)
        }
        if local_refs:
            selected_refs = local_refs
            ref_strategy = "local_refs"
        else:
            selected_refs = collect_leaf_refs(root)
            ref_strategy = "leaf_refs"
    if selected_refs == {"*"} and not has_select_star:
        expanded_refs = {
            ref
            for ref in collect_all_refs(root)
            if ref != "*"
            and ref != output_name
            and ref != f"{dataset.name}.{output_name}"
            and try_split_ref(ref) is not None
        }
        if expanded_refs:
            selected_refs = expanded_refs
            ref_strategy = "expanded_star"

    pre_filter_refs = frozenset(selected_refs)
    cte_chain, cte_local_aliases = extract_cte_context(
        set(selected_refs),
        cte_names=cte_name_set,
        alias_map=alias_map,
    )

    if (
        has_union
        and ref_strategy != "union_branches"
        and not union_refs_are_safe_to_resolve(
            set(selected_refs),
            statement_analysis=statement_analysis,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_name_set,
            registry=registry,
            dataset=dataset,
        )
    ):
        return ColumnLineageResolution(
            output_column=output_name,
            ref_selection_strategy="skipped_union",
            pre_filter_refs=pre_filter_refs,
            post_filter_refs=frozenset(),
            mapped_edges=empty,
            star_suppressed=False,
            warning_code=None,
            cte_chain=cte_chain,
            cte_local_aliases=cte_local_aliases,
        )

    if selected_refs != {"*"}:
        original_selected_refs = set(selected_refs)
        filtered_refs = filter_value_lineage_refs(
            root,
            selected_refs,
            dialect=dialect,
            analysis=statement_analysis,
        )
        if not filtered_refs:
            filtered_refs = {
                ref
                for ref in original_selected_refs
                if (parsed := try_split_ref(ref)) is not None
                and normalize_identifier_part(parsed[1]) == normalized_output
            }
        if not filtered_refs:
            warning_code = (
                "unresolved_star_source"
                if "*" in original_selected_refs
                else "unresolved_output_source"
            )
            return ColumnLineageResolution(
                output_column=output_name,
                ref_selection_strategy=ref_strategy,
                pre_filter_refs=pre_filter_refs,
                post_filter_refs=frozenset(),
                mapped_edges=empty,
                star_suppressed=False,
                warning_code=warning_code,
                cte_chain=cte_chain,
                cte_local_aliases=cte_local_aliases,
            )
        selected_refs = filtered_refs
        if (
            ref_strategy == "union_branches"
            and statement_analysis is not None
        ):
            selected_refs = derived_audit_column_supplement(
                set(selected_refs),
                root=root,
                analysis=statement_analysis,
            )

    post_filter_refs = frozenset(selected_refs)

    if (
        aliased_table_star
        and selected_refs
        and refs_target_only_root_datasets(selected_refs, project=project)
        and refs_confined_to_qualified_star_aliases(
            selected_refs,
            statement_analysis=statement_analysis,
            alias_map=alias_map,
        )
    ):
        return ColumnLineageResolution(
            output_column=output_name,
            ref_selection_strategy=ref_strategy,
            pre_filter_refs=pre_filter_refs,
            post_filter_refs=post_filter_refs,
            mapped_edges=empty,
            star_suppressed=False,
            warning_code="unresolved_output_source",
            cte_chain=cte_chain,
            cte_local_aliases=cte_local_aliases,
            warning_message=(
                "Alias-qualified table star projection did not resolve to a "
                f"concrete local dataset for output column {dataset.name}.{output_name}."
            ),
        )

    if not selected_refs:
        return ColumnLineageResolution(
            output_column=output_name,
            ref_selection_strategy=ref_strategy,
            pre_filter_refs=pre_filter_refs,
            post_filter_refs=post_filter_refs,
            mapped_edges=empty,
            star_suppressed=False,
            warning_code="unresolved_output_source",
            cte_chain=cte_chain,
            cte_local_aliases=cte_local_aliases,
        )

    if selected_refs == {"*"} and has_select_star:
        registry_resolution = registry_proven_star_resolution(
            output_name=output_name,
            dataset=dataset,
            project=project,
            lineage_schema=lineage_schema,
            statement_analysis=statement_analysis,
            registry=registry,
            pre_filter_refs=pre_filter_refs,
            post_filter_refs=post_filter_refs,
            cte_chain=cte_chain,
            cte_local_aliases=cte_local_aliases,
        )
        if registry_resolution is not None:
            return registry_resolution

    mapped: set[tuple[str, str, str, str]] = set()
    for leaf_ref in selected_refs:
        if leaf_ref == "*":
            return ColumnLineageResolution(
                output_column=output_name,
                ref_selection_strategy=ref_strategy,
                pre_filter_refs=pre_filter_refs,
                post_filter_refs=post_filter_refs,
                mapped_edges=empty,
                star_suppressed=False,
                warning_code="unresolved_star_source",
                cte_chain=cte_chain,
                cte_local_aliases=cte_local_aliases,
            )
        parsed_ref = try_split_ref(leaf_ref)
        if parsed_ref is None:
            continue
        parent_name, source_column = parsed_ref
        parent_key = normalize_identifier_part(parent_name)
        if (
            parent_key in cte_name_set
            and statement_analysis is not None
            and cte_projected_column_is_literal(
                parent_key,
                source_column,
                analysis=statement_analysis,
            )
        ):
            continue
        ref_edges = resolve_ref_to_mapped_edges(
            leaf_ref,
            output_name=output_name,
            dataset_name=dataset.name,
            statement_analysis=statement_analysis,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_name_set,
            registry=registry,
        )
        if ref_edges:
            mapped.update(ref_edges)
            continue
        if not parent_is_resolvable(
            parent_name,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_name_set,
            registry=registry,
        ):
            continue

    if (
        not mapped
        and statement_analysis is not None
        and root.expression is not None
    ):
        outer = outer_select(statement_analysis.statement)
        expression = unwrap_alias(root.expression)
        if expression is not None and not isinstance(expression, exp.Column):
            for relation, source_column in expression_column_refs(
                expression,
                analysis=statement_analysis,
                select=outer if isinstance(outer, exp.Select) else None,
            ):
                ref = normalize_identifier(f"{relation}.{source_column}")
                mapped.update(
                    resolve_ref_to_mapped_edges(
                        ref,
                        output_name=output_name,
                        dataset_name=dataset.name,
                        statement_analysis=statement_analysis,
                        project=project,
                        alias_map=alias_map,
                        table_references=table_references,
                        cte_names=cte_name_set,
                        registry=registry,
                    )
                )

    mapped = restrict_mapped_edges_for_outer_union_first_branch(
        mapped,
        output_name=output_name,
        root=root,
        analysis=statement_analysis,
        project=project,
        alias_map=alias_map,
        table_references=table_references,
        cte_names=cte_name_set,
        registry=registry,
    )

    if not mapped:
        return ColumnLineageResolution(
            output_column=output_name,
            ref_selection_strategy=ref_strategy,
            pre_filter_refs=pre_filter_refs,
            post_filter_refs=post_filter_refs,
            mapped_edges=empty,
            star_suppressed=False,
            warning_code="unresolved_output_source",
            cte_chain=cte_chain,
            cte_local_aliases=cte_local_aliases,
        )

    return ColumnLineageResolution(
        output_column=output_name,
        ref_selection_strategy=ref_strategy,
        pre_filter_refs=pre_filter_refs,
        post_filter_refs=post_filter_refs,
        mapped_edges=frozenset(mapped),
        star_suppressed=False,
        warning_code=None,
        cte_chain=cte_chain,
        cte_local_aliases=cte_local_aliases,
    )


