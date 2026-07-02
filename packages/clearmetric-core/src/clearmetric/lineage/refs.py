"""Qualified lineage ref parsing and sqlglot subtree traversal."""

from __future__ import annotations

from clearmetric.core import (
    CanonicalIdError,
    normalize_identifier,
    normalize_identifier_part,
    split_qualified_identifier,
)
from sqlglot.lineage import Node as SqlglotLineageNode

from .errors import LineageContractError
from .loaders import ProjectDataset, ProjectInput
from .sql_analyzer import qualified_star_alias_keys

__all__ = [
    "collect_all_refs",
    "collect_immediate_upstream_refs",
    "collect_leaf_refs",
    "downstream_is_star_only",
    "expand_unqualified_column_ref",
    "is_derived_scope_name",
    "is_local_ref",
    "local_model_sources_root",
    "refs_confined_to_qualified_star_aliases",
    "refs_from_lineage_subtree",
    "refs_target_only_root_datasets",
    "remap_root_sources_to_local_deps",
    "split_ref",
    "try_split_ref",
]


def split_ref(reference: str) -> tuple[str, str]:
    parts = split_qualified_identifier(reference)
    if len(parts) < 2:
        raise LineageContractError(
            f"Expected qualified lineage reference, got {reference!r}."
        )
    return ".".join(parts[:-1]), parts[-1]


def try_split_ref(reference: str) -> tuple[str, str] | None:
    try:
        return split_ref(reference)
    except LineageContractError:
        return None


def collect_immediate_upstream_refs(
    root: SqlglotLineageNode,
    *,
    project: ProjectInput,
    dataset: ProjectDataset,
    alias_map: dict[str, str],
    shadowed_aliases: dict[str, str],
    cte_names: set[str],
    known_relation_names: set[str],
    schema: dict[str, dict[str, str]],
) -> set[str]:
    refs: set[str] = set()
    for child in root.downstream:
        refs.update(
            refs_from_lineage_subtree(
                child,
                project=project,
                dataset=dataset,
                alias_map=alias_map,
                shadowed_aliases=shadowed_aliases,
                cte_names=cte_names,
                schema=schema,
            )
        )
    return remap_root_sources_to_local_deps(
        refs,
        project=project,
        dataset=dataset,
        known_relation_names=known_relation_names,
        schema=schema,
    )


def downstream_is_star_only(node: SqlglotLineageNode) -> bool:
    if not node.downstream:
        return True
    for child in node.downstream:
        if child.name.strip() == "*":
            continue
        if try_split_ref(child.name) is not None:
            return False
        if not downstream_is_star_only(child):
            return False
    return True


def refs_from_lineage_subtree(
    node: SqlglotLineageNode,
    *,
    project: ProjectInput,
    dataset: ProjectDataset,
    alias_map: dict[str, str],
    shadowed_aliases: dict[str, str] | None = None,
    cte_names: set[str],
    schema: dict[str, dict[str, str]] | None = None,
    seen: set[int] | None = None,
    active_cte: str | None = None,
    preserve_cte_scope: bool = False,
) -> set[str]:
    if seen is None:
        seen = set()
    node_id = id(node)
    if node_id in seen:
        return set()
    seen.add(node_id)
    parsed = try_split_ref(node.name)
    if parsed is not None:
        parent_name, column_name = parsed
        parent_key = normalize_identifier_part(parent_name)
        scoped_aliases = (
            alias_map
            if active_cte is not None
            else {**alias_map, **(shadowed_aliases or {})}
        )
        if parent_key in scoped_aliases:
            parent_key = scoped_aliases[parent_key]
        if parent_key in cte_names or is_derived_scope_name(
            parent_key,
            project=project,
            alias_map=alias_map,
            cte_names=cte_names,
            schema=schema or {},
        ):
            scoped_refs: set[str] = set()
            for child in node.downstream:
                scoped_refs.update(
                    refs_from_lineage_subtree(
                        child,
                        project=project,
                        dataset=dataset,
                        alias_map=alias_map,
                        shadowed_aliases=shadowed_aliases,
                        cte_names=cte_names,
                        schema=schema,
                        seen=seen,
                        active_cte=parent_key,
                        preserve_cte_scope=preserve_cte_scope,
                    )
                )
            if not scoped_refs and (
                not node.downstream or downstream_is_star_only(node)
            ):
                return {normalize_identifier(f"{parent_key}.{column_name}")}
            return scoped_refs
        return {normalize_identifier(f"{parent_key}.{column_name}")}
    if node.downstream:
        downstream_refs: set[str] = set()
        for child in node.downstream:
            downstream_refs.update(
                refs_from_lineage_subtree(
                    child,
                    project=project,
                    dataset=dataset,
                    alias_map=alias_map,
                    shadowed_aliases=shadowed_aliases,
                    cte_names=cte_names,
                    schema=schema,
                    seen=seen,
                    active_cte=active_cte,
                    preserve_cte_scope=preserve_cte_scope,
                )
            )
        return downstream_refs
    if preserve_cte_scope and active_cte is not None:
        try:
            column_key = normalize_identifier_part(node.name)
        except CanonicalIdError:
            column_key = None
        if column_key is not None:
            return {normalize_identifier(f"{active_cte}.{column_key}")}
    return expand_unqualified_column_ref(
        node.name,
        project=project,
        dataset=dataset,
    )


def expand_unqualified_column_ref(
    column_name: str,
    *,
    project: ProjectInput,
    dataset: ProjectDataset,
) -> set[str]:
    try:
        column_key = normalize_identifier_part(column_name)
    except CanonicalIdError:
        return set()
    matches: set[str] = set()
    for dependency_name in dataset.dependency_names:
        dependency = project.datasets.get(dependency_name)
        if dependency is None:
            continue
        declared = {
            normalize_identifier_part(name) for name in dependency.declared_columns
        }
        if column_key in declared:
            matches.add(normalize_identifier(f"{dependency_name}.{column_key}"))
    return matches


def remap_root_sources_to_local_deps(
    refs: set[str],
    *,
    project: ProjectInput,
    dataset: ProjectDataset,
    known_relation_names: set[str],
    schema: dict[str, dict[str, str]],
) -> set[str]:
    remapped: set[str] = set()
    for ref in refs:
        parsed = try_split_ref(ref)
        if parsed is None:
            continue
        parent_name, column_name = parsed
        parent_key = normalize_identifier_part(parent_name)
        if parent_key not in schema:
            remapped.add(normalize_identifier(f"{parent_key}.{column_name}"))
            continue
        if parent_key in known_relation_names:
            remapped.add(normalize_identifier(f"{parent_key}.{column_name}"))
            continue
        local_matches = [
            dependency_name
            for dependency_name in dataset.dependency_names
            if local_model_sources_root(
                project,
                dependency_name=dependency_name,
                root_name=parent_key,
            )
        ]
        if len(local_matches) == 1:
            remapped.add(normalize_identifier(f"{local_matches[0]}.{column_name}"))
            continue
        remapped.add(normalize_identifier(f"{parent_key}.{column_name}"))
    return remapped


def local_model_sources_root(
    project: ProjectInput,
    *,
    dependency_name: str,
    root_name: str,
) -> bool:
    dependency = project.datasets.get(dependency_name)
    if dependency is None or dependency.kind != "local":
        return False
    return root_name in {
        normalize_identifier_part(name) for name in dependency.dependency_names
    }


def is_derived_scope_name(
    parent_key: str,
    *,
    project: ProjectInput,
    alias_map: dict[str, str],
    cte_names: set[str],
    schema: dict[str, dict[str, str]],
) -> bool:
    if parent_key in cte_names:
        return True
    if parent_key in project.datasets or parent_key in schema:
        return False
    if parent_key in alias_map:
        return False
    return True


def refs_target_only_root_datasets(
    refs: set[str],
    *,
    project: ProjectInput,
) -> bool:
    if not refs:
        return False
    for ref in refs:
        parsed = try_split_ref(ref)
        if parsed is None:
            return False
        parent_name, _column_name = parsed
        parent_key = normalize_identifier_part(parent_name)
        dataset = project.datasets.get(parent_key)
        if dataset is None or dataset.kind != "root":
            return False
    return True


def refs_confined_to_qualified_star_aliases(
    refs: set[str],
    *,
    statement_analysis,
    alias_map: dict[str, str],
) -> bool:
    """True when refs trace only through qualified-star aliases (e.g. ``enc.*``), not join tables."""
    star_aliases = qualified_star_alias_keys(statement_analysis)
    if not star_aliases:
        return False
    for ref in refs:
        parsed = try_split_ref(ref)
        if parsed is None:
            return False
        parent_name, _column_name = parsed
        parent_key = normalize_identifier_part(parent_name)
        if parent_key in star_aliases:
            continue
        source_alias: str | None = None
        for alias, target in alias_map.items():
            normalized_alias = normalize_identifier_part(alias)
            normalized_target = normalize_identifier_part(target)
            if parent_key in {normalized_alias, normalized_target}:
                source_alias = normalized_alias
                break
        if source_alias in star_aliases:
            continue
        return False
    return True


def collect_leaf_refs(
    node: SqlglotLineageNode, *, _seen: set[int] | None = None
) -> set[str]:
    if _seen is None:
        _seen = set()
    node_id = id(node)
    if node_id in _seen:
        return set()
    _seen.add(node_id)
    if not node.downstream:
        return {node.name}
    refs: set[str] = set()
    for child in node.downstream:
        refs.update(collect_leaf_refs(child, _seen=_seen))
    return refs


def collect_all_refs(
    node: SqlglotLineageNode, *, _seen: set[int] | None = None
) -> set[str]:
    if _seen is None:
        _seen = set()
    node_id = id(node)
    if node_id in _seen:
        return set()
    _seen.add(node_id)
    refs = {node.name}
    for child in node.downstream:
        refs.update(collect_all_refs(child, _seen=_seen))
    return refs


def is_local_ref(
    reference: str,
    *,
    project: ProjectInput,
    current_dataset: str,
) -> bool:
    if reference == "*":
        return False
    try:
        parent_name, _column_name = split_ref(reference)
    except LineageContractError:
        return False
    if parent_name == current_dataset or parent_name not in project.datasets:
        return False
    return project.datasets[parent_name].kind == "local"
