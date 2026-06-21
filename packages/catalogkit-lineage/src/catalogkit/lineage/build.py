"""Artifact assembly for catalogkit-lineage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from catalogkit.core import (
    CatalogArtifact,
    Edge,
    Evidence,
    Node,
    Warning,
    column_id,
    leaf_name,
    merge,
    normalize_identifier,
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
    detect_select_star,
    filter_value_lineage_refs,
    list_table_references,
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
        _add_query_warnings(warnings, dataset, dialect=dialect)
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


def _add_query_warnings(
    warnings: list[Warning],
    dataset: ProjectDataset,
    *,
    dialect: str,
) -> None:
    try:
        has_select_star = detect_select_star(dataset.sql or "", dialect=dialect)
    except LineageInputError:
        return
    if has_select_star:
        _emit_column_warning(
            warnings,
            code="select_star",
            dataset=dataset,
            column_name=None,
            message="SELECT * was detected; output mapping may stay warning-rich.",
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
    )
    try:
        known_relation_names = {
            normalize_identifier(reference)
            for reference in list_table_references(dataset.sql or "", dialect=dialect)
        }
    except LineageInputError:
        known_relation_names = set()
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
        state.output_map_keys.add(output_name)
        _add_column_node(nodes_by_id, dataset.name, output_name, dataset.evidence_file)
        source_id = column_id(dataset.name, output_name)
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
            parent_name, source_column = _split_ref(leaf_ref)
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
            state.columns_with_edges.add(output_name)
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
            ),
        )
        column_names = sorted({*dataset.declared_columns, *state.output_map_keys})
        for column_name in column_names:
            subject_id = column_id(dataset.name, column_name)
            if column_name in state.columns_with_edges or _warning_exists(
                warnings,
                code="unresolved_lineage",
                subject_id=subject_id,
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
        state.columns_with_warnings.add(column_name)


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
