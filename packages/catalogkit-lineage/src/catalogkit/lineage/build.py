"""Artifact assembly for catalogkit-lineage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from catalogkit.core import (
    CanonicalIdError,
    CatalogArtifact,
    Edge,
    Evidence,
    Node,
    Warning,
    column_id,
    leaf_name,
    merge,
    normalize_identifier,
    normalize_identifier_part,
    schema_name,
    split_qualified_identifier,
    table_id,
)
from sqlglot.lineage import Node as SqlglotLineageNode
from sqlglot.lineage import lineage

from .errors import LineageContractError, LineageInputError
from .graph import (
    column_selection_from_id,
    derives_from_edges,
    walk_downstream,
    walk_upstream,
    warnings_for_subject,
)
from .loaders import ProjectDataset, ProjectInput
from .models import LineageMap, LineageSummary, TraversalResult
from .sql_analyzer import (
    analyze_sql_statement,
    filter_value_lineage_refs,
    has_select_star_projection,
    is_star_suppressed_output,
    quoted_alias_output_columns,
    star_expansion_policy,
    uses_aliased_table_star,
)


@dataclass(frozen=True)
class BuiltLineage:
    artifact: CatalogArtifact
    summary: LineageSummary


@dataclass
class DatasetResolutionState:
    output_map_keys: set[str]
    columns_with_edges: set[str]
    columns_with_warnings: set[str]
    columns_star_suppressed: set[str]


def build_catalog_artifact_from_project(
    project: ProjectInput,
    *,
    dialect: str,
) -> CatalogArtifact:
    return _build_lineage(project, dialect=dialect).artifact


def build_lineage_map_from_project(
    project: ProjectInput,
    *,
    dialect: str,
) -> LineageMap:
    built = _build_lineage(project, dialect=dialect)
    return LineageMap(
        version=built.artifact.version,
        summary=built.summary,
        nodes=built.artifact.nodes,
        edges=built.artifact.edges,
        warnings=built.artifact.warnings,
    )


def trace_upstream_from_project(
    project: ProjectInput,
    *,
    dialect: str,
    selection: str,
) -> TraversalResult:
    artifact = build_catalog_artifact_from_project(project, dialect=dialect)
    return trace_upstream_from_artifact(artifact, selection=selection)


def trace_downstream_from_project(
    project: ProjectInput,
    *,
    dialect: str,
    selection: str,
) -> TraversalResult:
    artifact = build_catalog_artifact_from_project(project, dialect=dialect)
    return trace_downstream_from_artifact(artifact, selection=selection)


def trace_upstream_from_artifact(
    artifact: CatalogArtifact,
    *,
    selection: str,
) -> TraversalResult:
    selection_id = _selection_to_column_id(selection)
    _require_column_selection(artifact, selection=selection, selection_id=selection_id)
    return TraversalResult(
        selection=selection,
        selection_id=selection_id,
        related_ids=walk_upstream(selection_id, artifact),
        warnings=warnings_for_subject(
            artifact,
            selection_id,
            dataset_name=selection.rsplit(".", 1)[0],
        ),
    )


def trace_downstream_from_artifact(
    artifact: CatalogArtifact,
    *,
    selection: str,
) -> TraversalResult:
    selection_id = _selection_to_column_id(selection)
    _require_column_selection(artifact, selection=selection, selection_id=selection_id)
    return TraversalResult(
        selection=selection,
        selection_id=selection_id,
        related_ids=walk_downstream(selection_id, artifact),
        warnings=warnings_for_subject(
            artifact,
            selection_id,
            dataset_name=selection.rsplit(".", 1)[0],
        ),
    )


def build_openlineage_export_from_project(
    project: ProjectInput,
    *,
    dialect: str,
) -> dict[str, Any]:
    artifact = build_catalog_artifact_from_project(project, dialect=dialect)
    return build_openlineage_export_from_artifact(artifact, job_name=project.label)


def build_openlineage_export_from_artifact(
    artifact: CatalogArtifact,
    *,
    job_name: str,
) -> dict[str, Any]:
    input_fields_by_output: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
    for edge in derives_from_edges(artifact):
        output_dataset, output_column = column_selection_from_id(edge.source_id)
        input_dataset, input_column = column_selection_from_id(edge.target_id)
        input_fields_by_output.setdefault((output_dataset, output_column), set()).add(
            ("catalogkit", input_dataset, input_column)
        )

    export_entries = [
        {
            "dataset": output_dataset,
            "column": output_column,
            "inputFields": [
                {
                    "namespace": namespace,
                    "name": input_dataset,
                    "field": input_column,
                }
                for namespace, input_dataset, input_column in sorted(input_fields)
            ],
        }
        for (output_dataset, output_column), input_fields in sorted(
            input_fields_by_output.items()
        )
    ]

    datasets = [
        {
            "namespace": "catalogkit",
            "name": node.qualified_name or node.name,
            "kind": node.kind,
        }
        for node in sorted(
            artifact.nodes, key=lambda item: item.qualified_name or item.name
        )
        if node.kind == "table"
    ]

    return {
        "job": {
            "namespace": "catalogkit",
            "name": job_name,
        },
        "datasets": datasets,
        "columnLineage": export_entries,
    }


def _build_lineage(project: ProjectInput, *, dialect: str) -> BuiltLineage:
    nodes_by_id: dict[str, Node] = {}
    edges: list[Edge] = []
    warnings: list[Warning] = []
    resolution_by_dataset: dict[str, DatasetResolutionState] = {}

    for dataset in project.datasets.values():
        _add_dataset_node(nodes_by_id, dataset)
        for column_name in dataset.declared_columns:
            _add_column_node(
                nodes_by_id, dataset.name, column_name, dataset.evidence_file
            )

    for dataset in project.datasets.values():
        if dataset.kind != "local":
            continue
        _add_dependency_edges(nodes_by_id, edges, dataset, project=project)
        resolution_by_dataset[dataset.name] = _add_lineage_edges(
            nodes_by_id,
            edges,
            warnings,
            dataset,
            project=project,
            dialect=dialect,
        )

    _reconcile_column_coverage(
        warnings,
        project=project,
        resolution_by_dataset=resolution_by_dataset,
    )

    artifact = merge(
        CatalogArtifact(
            nodes=sorted(nodes_by_id.values(), key=lambda item: item.id),
            edges=edges,
            warnings=warnings,
        )
    )
    column_count = sum(1 for node in artifact.nodes if node.kind == "column")
    dataset_count = sum(1 for node in artifact.nodes if node.kind == "table")
    root_dataset_count = sum(
        1 for dataset in project.datasets.values() if dataset.kind == "root"
    )
    summary = LineageSummary(
        dialect=dialect,
        input_kind=project.input_kind,
        dataset_count=dataset_count,
        root_dataset_count=root_dataset_count,
        column_count=column_count,
        warning_count=len(artifact.warnings),
    )
    return BuiltLineage(artifact=artifact, summary=summary)


def _add_dataset_node(nodes_by_id: dict[str, Node], dataset: ProjectDataset) -> None:
    dataset_id = table_id(dataset.name)
    if dataset_id in nodes_by_id:
        return
    nodes_by_id[dataset_id] = Node(
        id=dataset_id,
        kind="table",
        name=leaf_name(dataset.name),
        qualified_name=dataset.name,
        schema=schema_name(dataset.name),
        evidence=_dataset_evidence(dataset),
    )


def _dataset_evidence(dataset: ProjectDataset) -> list[Evidence]:
    if not dataset.evidence_file:
        return []
    return [
        Evidence(
            file=dataset.evidence_file,
            expression=dataset.name,
            confidence="high",
        )
    ]


def _add_column_node(
    nodes_by_id: dict[str, Node],
    dataset_name: str,
    column_name: str,
    evidence_file: str | None,
) -> None:
    node_id = column_id(dataset_name, column_name)
    if node_id in nodes_by_id:
        return
    evidence = []
    if evidence_file:
        evidence.append(
            Evidence(
                file=evidence_file,
                expression=f"{dataset_name}.{column_name}",
                confidence="high",
            )
        )
    nodes_by_id[node_id] = Node(
        id=node_id,
        kind="column",
        name=column_name,
        qualified_name=f"{dataset_name}.{column_name}",
        schema=schema_name(dataset_name),
        evidence=evidence,
    )


def _add_dependency_edges(
    nodes_by_id: dict[str, Node],
    edges: list[Edge],
    dataset: ProjectDataset,
    *,
    project: ProjectInput,
) -> None:
    source_id = table_id(dataset.name)
    for dependency_name in dataset.dependency_names:
        _add_dataset_node(
            nodes_by_id,
            ProjectDataset(
                name=dependency_name,
                kind=(
                    project.datasets[dependency_name].kind
                    if dependency_name in project.datasets
                    else "root"
                ),
                sql=None,
                dependency_names=(),
                declared_columns=(),
                evidence_file=None,
            ),
        )
        edges.append(
            Edge(
                kind="depends_on",
                source_id=source_id,
                target_id=table_id(dependency_name),
                label="depends_on",
                evidence=[
                    Evidence(
                        file=dataset.evidence_file,
                        expression=dependency_name,
                        confidence="high",
                    )
                ]
                if dataset.evidence_file
                else [],
            )
        )


def _add_lineage_edges(
    nodes_by_id: dict[str, Node],
    edges: list[Edge],
    warnings: list[Warning],
    dataset: ProjectDataset,
    *,
    project: ProjectInput,
    dialect: str,
) -> DatasetResolutionState:
    state = DatasetResolutionState(
        output_map_keys=set(),
        columns_with_edges=set(),
        columns_with_warnings=set(),
        columns_star_suppressed=set(),
    )
    try:
        statement_analysis = analyze_sql_statement(dataset.sql or "", dialect=dialect)
    except LineageInputError:
        statement_analysis = None
    if statement_analysis is None:
        known_relation_names: set[str] = set()
        alias_map: dict[str, str] = {}
        cte_name_set: set[str] = set()
        aliased_table_star = False
        star_policy = None
        quoted_outputs: frozenset[str] = frozenset()
        has_union = False
        has_select_star = False
    else:
        known_relation_names = {
            normalize_identifier(reference)
            for reference in statement_analysis.table_references
        }
        alias_map = statement_analysis.alias_map
        cte_name_set = statement_analysis.cte_names
        aliased_table_star = uses_aliased_table_star(statement_analysis)
        has_select_star = has_select_star_projection(statement_analysis)
        if has_select_star:
            _emit_column_warning(
                warnings,
                code="select_star",
                dataset=dataset,
                column_name=None,
                message="SELECT * was detected; output mapping may stay warning-rich.",
            )
        star_policy = (
            star_expansion_policy(statement_analysis, project=project)
            if has_select_star
            else None
        )
        quoted_outputs = quoted_alias_output_columns(statement_analysis)
        has_union = statement_analysis.has_union
    try:
        output_map = lineage(
            None,
            dataset.sql or "",
            schema=project.root_schema(),
            sources=project.sources_for(dataset.name),
            dialect=dialect,
        )
    except Exception as exc:  # pragma: no cover - exercised via failure-path tests
        _emit_column_warning(
            warnings,
            code="lineage_resolution_failed",
            dataset=dataset,
            column_name=None,
            message=f"Lineage resolution failed for dataset {dataset.name!r}: {exc}",
        )
        return state

    if not isinstance(output_map, dict):
        raise LineageContractError(
            "catalogkit-lineage expected sqlglot.lineage(None, ...) to return a dict."
        )

    for output_name, root in sorted(output_map.items(), key=lambda item: item[0]):
        if output_name == "*":
            _emit_column_warning(
                warnings,
                code="unresolved_star_source",
                dataset=dataset,
                column_name=None,
                message=(
                    f"Lineage output expansion stayed at '*' for dataset {dataset.name!r}."
                ),
            )
            continue
        normalized_output = normalize_identifier_part(output_name)
        state.output_map_keys.add(normalized_output)
        _add_column_node(nodes_by_id, dataset.name, output_name, dataset.evidence_file)
        if has_union:
            continue
        if has_select_star and is_star_suppressed_output(output_name, star_policy):
            state.columns_star_suppressed.add(normalized_output)
            continue
        if normalized_output in quoted_outputs:
            _emit_column_warning(
                warnings,
                code="unresolved_lineage",
                dataset=dataset,
                column_name=output_name,
                message=(
                    "Quoted output identifier declined for value-lineage edge emission on "
                    f"{dataset.name}.{output_name}."
                ),
                state=state,
            )
            continue
        source_id = column_id(dataset.name, output_name)
        immediate_refs = _collect_immediate_upstream_refs(
            root,
            project=project,
            dataset=dataset,
            alias_map=alias_map,
            cte_names=cte_name_set,
            known_relation_names=known_relation_names,
        )
        if immediate_refs:
            selected_refs = immediate_refs
        else:
            all_refs = {
                ref
                for ref in _collect_all_refs(root)
                if ref != output_name and ref != f"{dataset.name}.{output_name}"
            }
            local_refs = {
                ref
                for ref in all_refs
                if _is_local_ref(ref, project=project, current_dataset=dataset.name)
            }
            selected_refs = local_refs or _collect_leaf_refs(root)
        if selected_refs != {"*"}:
            original_selected_refs = set(selected_refs)
            filtered_refs = filter_value_lineage_refs(
                root,
                selected_refs,
                dialect=dialect,
            )
            if not filtered_refs:
                if "*" in original_selected_refs:
                    _emit_column_warning(
                        warnings,
                        code="unresolved_star_source",
                        dataset=dataset,
                        column_name=output_name,
                        message=(
                            "Value-lineage filtering removed all upstream refs and "
                            f"only '*' remained for {dataset.name}.{output_name}."
                        ),
                        state=state,
                    )
                else:
                    _emit_column_warning(
                        warnings,
                        code="unresolved_output_source",
                        dataset=dataset,
                        column_name=output_name,
                        message=(
                            "Value-lineage filtering removed all upstream refs for "
                            f"output column {dataset.name}.{output_name}."
                        ),
                        state=state,
                    )
                continue
            selected_refs = filtered_refs
        if (
            aliased_table_star
            and selected_refs
            and _refs_target_only_root_datasets(selected_refs, project=project)
        ):
            _emit_column_warning(
                warnings,
                code="unresolved_output_source",
                dataset=dataset,
                column_name=output_name,
                message=(
                    "Alias-qualified table star projection did not resolve to a "
                    f"concrete local dataset for output column {dataset.name}.{output_name}."
                ),
                state=state,
            )
            continue
        if not selected_refs:
            _emit_column_warning(
                warnings,
                code="unresolved_output_source",
                dataset=dataset,
                column_name=output_name,
                message=(
                    "Lineage resolved no upstream value leaves for output column "
                    f"{dataset.name}.{output_name}."
                ),
                state=state,
            )
            continue
        for leaf_ref in sorted(selected_refs):
            if leaf_ref == "*":
                _emit_column_warning(
                    warnings,
                    code="unresolved_star_source",
                    dataset=dataset,
                    column_name=output_name,
                    message=(
                        "Lineage leaf expansion stayed at '*' for output column "
                        f"{dataset.name}.{output_name}."
                    ),
                    state=state,
                )
                continue
            parsed_ref = _try_split_ref(leaf_ref)
            if parsed_ref is None:
                continue
            parent_name, source_column = parsed_ref
            if (
                parent_name not in project.datasets
                and parent_name not in project.root_schema()
                and normalize_identifier(parent_name) not in known_relation_names
            ):
                _emit_column_warning(
                    warnings,
                    code="unresolved_output_source",
                    dataset=dataset,
                    column_name=output_name,
                    message=(
                        f"Lineage resolved relation alias {parent_name!r} instead of a "
                        "concrete upstream dataset for output column "
                        f"{dataset.name}.{output_name}."
                    ),
                    state=state,
                )
                continue
            _add_dataset_node(
                nodes_by_id,
                ProjectDataset(
                    name=parent_name,
                    kind="root"
                    if parent_name not in project.datasets
                    else project.datasets[parent_name].kind,
                    sql=None,
                    dependency_names=(),
                    declared_columns=(),
                    evidence_file=None,
                ),
            )
            _add_column_node(nodes_by_id, parent_name, source_column, None)
            edges.append(
                Edge(
                    kind="derives_from",
                    source_id=source_id,
                    target_id=column_id(parent_name, source_column),
                    label="derives_from",
                    evidence=[
                        Evidence(
                            file=dataset.evidence_file,
                            expression=leaf_ref,
                            confidence="medium",
                        )
                    ]
                    if dataset.evidence_file
                    else [],
                )
            )
            state.columns_with_edges.add(normalized_output)
    return state


def _reconcile_column_coverage(
    warnings: list[Warning],
    *,
    project: ProjectInput,
    resolution_by_dataset: dict[str, DatasetResolutionState],
) -> None:
    for dataset in project.datasets.values():
        if dataset.kind != "local":
            continue
        state = resolution_by_dataset.get(
            dataset.name,
            DatasetResolutionState(
                output_map_keys=set(),
                columns_with_edges=set(),
                columns_with_warnings=set(),
                columns_star_suppressed=set(),
            ),
        )
        column_names = sorted({*dataset.declared_columns, *state.output_map_keys})
        for column_name in column_names:
            normalized_column = normalize_identifier_part(column_name)
            subject_id = column_id(dataset.name, column_name)
            if (
                normalized_column in state.columns_with_edges
                or normalized_column in state.columns_star_suppressed
                or _warning_exists(
                    warnings,
                    code="unresolved_lineage",
                    subject_id=subject_id,
                )
            ):
                continue
            _emit_column_warning(
                warnings,
                code="unresolved_lineage",
                dataset=dataset,
                column_name=column_name,
                message=(
                    "Lineage could not be resolved for output column "
                    f"{dataset.name}.{column_name}."
                ),
                state=state,
            )


def _emit_column_warning(
    warnings: list[Warning],
    *,
    code: str,
    dataset: ProjectDataset,
    column_name: str | None,
    message: str,
    state: DatasetResolutionState | None = None,
) -> None:
    warnings.append(
        Warning(
            code=code,
            message=message,
            location=dataset.evidence_file,
            subject_id=(
                column_id(dataset.name, column_name)
                if column_name is not None
                else None
            ),
        )
    )
    if state is not None and column_name is not None:
        state.columns_with_warnings.add(normalize_identifier_part(column_name))


def _warning_exists(
    warnings: list[Warning],
    *,
    code: str,
    subject_id: str,
) -> bool:
    return any(
        warning.code == code and warning.subject_id == subject_id
        for warning in warnings
    )


def _collect_immediate_upstream_refs(
    root: SqlglotLineageNode,
    *,
    project: ProjectInput,
    dataset: ProjectDataset,
    alias_map: dict[str, str],
    cte_names: set[str],
    known_relation_names: set[str],
) -> set[str]:
    refs: set[str] = set()
    for child in root.downstream:
        refs.update(
            _refs_from_lineage_subtree(
                child,
                project=project,
                dataset=dataset,
                alias_map=alias_map,
                cte_names=cte_names,
            )
        )
    return _remap_root_sources_to_local_deps(
        refs,
        project=project,
        dataset=dataset,
        known_relation_names=known_relation_names,
    )


def _refs_from_lineage_subtree(
    node: SqlglotLineageNode,
    *,
    project: ProjectInput,
    dataset: ProjectDataset,
    alias_map: dict[str, str],
    cte_names: set[str],
) -> set[str]:
    parsed = _try_split_ref(node.name)
    if parsed is not None:
        parent_name, column_name = parsed
        parent_key = normalize_identifier_part(parent_name)
        if parent_key in alias_map:
            parent_key = alias_map[parent_key]
        if parent_key in cte_names or _is_derived_scope_name(
            parent_key,
            project=project,
            alias_map=alias_map,
            cte_names=cte_names,
        ):
            scoped_refs: set[str] = set()
            for child in node.downstream:
                scoped_refs.update(
                    _refs_from_lineage_subtree(
                        child,
                        project=project,
                        dataset=dataset,
                        alias_map=alias_map,
                        cte_names=cte_names,
                    )
                )
            return scoped_refs
        return {normalize_identifier(f"{parent_key}.{column_name}")}
    if node.downstream:
        downstream_refs: set[str] = set()
        for child in node.downstream:
            downstream_refs.update(
                _refs_from_lineage_subtree(
                    child,
                    project=project,
                    dataset=dataset,
                    alias_map=alias_map,
                    cte_names=cte_names,
                )
            )
        return downstream_refs
    return _expand_unqualified_column_ref(
        node.name,
        project=project,
        dataset=dataset,
    )


def _expand_unqualified_column_ref(
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


def _remap_root_sources_to_local_deps(
    refs: set[str],
    *,
    project: ProjectInput,
    dataset: ProjectDataset,
    known_relation_names: set[str],
) -> set[str]:
    remapped: set[str] = set()
    root_schema = project.root_schema()
    for ref in refs:
        parsed = _try_split_ref(ref)
        if parsed is None:
            continue
        parent_name, column_name = parsed
        parent_key = normalize_identifier_part(parent_name)
        if parent_key not in root_schema:
            remapped.add(normalize_identifier(f"{parent_key}.{column_name}"))
            continue
        if parent_key in known_relation_names:
            remapped.add(normalize_identifier(f"{parent_key}.{column_name}"))
            continue
        local_matches = [
            dependency_name
            for dependency_name in dataset.dependency_names
            if _local_model_sources_root(
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


def _local_model_sources_root(
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


def _is_derived_scope_name(
    parent_key: str,
    *,
    project: ProjectInput,
    alias_map: dict[str, str],
    cte_names: set[str],
) -> bool:
    if parent_key in cte_names:
        return True
    if parent_key in project.datasets or parent_key in project.root_schema():
        return False
    if parent_key in alias_map:
        return False
    return True


def _refs_target_only_root_datasets(
    refs: set[str],
    *,
    project: ProjectInput,
) -> bool:
    if not refs:
        return False
    for ref in refs:
        parsed = _try_split_ref(ref)
        if parsed is None:
            return False
        parent_name, _column_name = parsed
        parent_key = normalize_identifier_part(parent_name)
        dataset = project.datasets.get(parent_key)
        if dataset is None or dataset.kind != "root":
            return False
    return True


def _collect_leaf_refs(node: SqlglotLineageNode) -> set[str]:
    if not node.downstream:
        return {node.name}
    refs: set[str] = set()
    for child in node.downstream:
        refs.update(_collect_leaf_refs(child))
    return refs


def _collect_all_refs(node: SqlglotLineageNode) -> set[str]:
    refs = {node.name}
    for child in node.downstream:
        refs.update(_collect_all_refs(child))
    return refs


def _is_local_ref(
    reference: str,
    *,
    project: ProjectInput,
    current_dataset: str,
) -> bool:
    if reference == "*":
        return False
    try:
        parent_name, _column_name = _split_ref(reference)
    except LineageContractError:
        return False
    if parent_name == current_dataset or parent_name not in project.datasets:
        return False
    return project.datasets[parent_name].kind == "local"


def _split_ref(reference: str) -> tuple[str, str]:
    parts = split_qualified_identifier(reference)
    if len(parts) < 2:
        raise LineageContractError(
            f"Expected qualified lineage reference, got {reference!r}."
        )
    return ".".join(parts[:-1]), parts[-1]


def _try_split_ref(reference: str) -> tuple[str, str] | None:
    try:
        return _split_ref(reference)
    except LineageContractError:
        return None


def _selection_to_column_id(selection: str) -> str:
    parent_name, column_name = _split_ref(selection)
    return column_id(parent_name, column_name)


def _require_column_selection(
    artifact: CatalogArtifact,
    *,
    selection: str,
    selection_id: str,
) -> None:
    if any(
        node.id == selection_id and node.kind == "column" for node in artifact.nodes
    ):
        return
    raise LineageInputError(
        f"Selection {selection!r} does not match any resolved lineage column."
    )
