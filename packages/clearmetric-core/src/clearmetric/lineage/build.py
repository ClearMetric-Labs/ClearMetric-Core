"""Artifact assembly for clearmetric-core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from clearmetric.core import (
    CanonicalIdError,
    CatalogArtifact,
    DerivationState,
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
from clearmetric.core.models import Confidence, DerivationStatus
from clearmetric.graph import dataset_from_location
from sqlglot import exp
from sqlglot.lineage import Node as SqlglotLineageNode

from .coverage import EXPANDED_STAR_CODE
from .errors import LineageContractError, LineageInputError
from .loaders import ProjectDataset, ProjectInput, dbt_aspect_for_dataset
from .models import LineageMap, LineageSummary
from .output_columns import infer_output_columns
from .resolver_status import (
    LineageResolutionAspect,
    ResolverStatusInput,
    derive_resolver_status,
    derive_type_status,
)
from .schema_registry import (
    MissingSchema,
    RelationSchema,
    SchemaRegistry,
)
from .sql_analyzer import (
    StarExpansionPolicy,
    _outer_select,
    _shadowed_outer_select_aliases,
    _single_bare_star_source_relation,
    _unwrap_alias,
    analyze_sql_statement,
    bare_star_column_upstream,
    cte_output_uses_secondary_join_source,
    cte_projected_column_is_literal,
    cte_single_source_base_relation,
    expression_column_refs,
    filter_value_lineage_refs,
    from_clause_base_relations,
    has_select_star_projection,
    is_macro_generated_union,
    is_star_suppressed_output,
    lineage_output_map,
    macro_union_schema_branch_refs_index,
    mixed_explicit_and_star_outer_select,
    qualified_star_alias_keys,
    quoted_alias_output_columns,
    resolve_star_suppressed_column_upstream,
    star_expansion_policy,
    star_suppressed_cte_column_resolves_outside_dependencies,
    union_branch_upstream_refs,
    union_has_null_padding_asymmetry,
    uses_outer_aliased_table_star,
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


@dataclass(frozen=True)
class ModelLineageResult:
    edges: frozenset[tuple[str, str, str, str]]
    warnings: tuple[Warning, ...]
    schema_at_build: dict[str, dict[str, str]]
    lineage_resolution: dict[str, object] | None = None
    resolver_status: str | None = None
    column_resolutions: tuple[ColumnLineageResolution, ...] = ()
    schema_metadata_at_build: dict[str, dict[str, object]] | None = None


@dataclass
class _TopoLineageBuild:
    resolution_by_dataset: dict[str, DatasetResolutionState]
    registry: SchemaRegistry
    per_model: dict[str, ModelLineageResult]
    nodes_by_id: dict[str, Node]
    edges: list[Edge]
    warnings: list[Warning]


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


def model_derives_from_edges(
    project: ProjectInput,
    dataset: ProjectDataset,
    *,
    schema: dict[str, dict[str, str]] | None = None,
    dialect: str,
) -> tuple[set[tuple[str, str, str, str]], list[Warning]]:
    nodes_by_id: dict[str, Node] = {}
    edges: list[Edge] = []
    warnings: list[Warning] = []
    registry = SchemaRegistry.from_project(project, dialect=dialect)
    infer_output_columns(dataset, registry, project=project, dialect=dialect)
    _add_lineage_edges(
        nodes_by_id,
        edges,
        warnings,
        dataset,
        project=project,
        dialect=dialect,
        registry=registry,
    )
    return set(_normalize_derives_from_edges(edges)), warnings


def edges_by_model_from_project(
    project: ProjectInput,
    *,
    dialect: str,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, ModelLineageResult]:
    return _run_topo_lineage(project, dialect=dialect, progress=progress).per_model


def _normalize_derives_from_edges(
    edges: list[Edge],
) -> list[tuple[str, str, str, str]]:
    normalized: list[tuple[str, str, str, str]] = []
    for edge in edges:
        if edge.kind != "derives_from":
            continue
        downstream_parts = edge.source_id.removeprefix("column:").rsplit(".", 1)
        upstream_parts = edge.target_id.removeprefix("column:").rsplit(".", 1)
        if len(downstream_parts) != 2 or len(upstream_parts) != 2:
            continue
        normalized.append(
            (
                upstream_parts[0],
                upstream_parts[1],
                downstream_parts[0],
                downstream_parts[1],
            )
        )
    return normalized


def _run_topo_lineage(
    project: ProjectInput,
    *,
    dialect: str,
    progress: Callable[[int, int, str], None] | None = None,
) -> _TopoLineageBuild:
    nodes_by_id: dict[str, Node] = {}
    edges: list[Edge] = []
    warnings: list[Warning] = list(project.type_warnings)
    resolution_by_dataset: dict[str, DatasetResolutionState] = {}
    per_model: dict[str, ModelLineageResult] = {}
    registry = SchemaRegistry.from_project(project, dialect=dialect)

    for dataset in project.datasets.values():
        _add_dataset_node(nodes_by_id, dataset)
        for column_name in dataset.declared_columns:
            _add_column_node(
                nodes_by_id, dataset.name, column_name, dataset.evidence_file
            )
        _emit_type_coverage_warnings(warnings, dataset, dialect=dialect)

    ordered_local, cyclic_local = _topological_local_datasets(project)

    for dataset in ordered_local:
        infer_output_columns(
            dataset,
            registry,
            project=project,
            dialect=dialect,
        )

    for index, dataset in enumerate(ordered_local, start=1):
        if progress is not None:
            progress(index, len(ordered_local), dataset.name)
        warning_start = len(warnings)
        edge_start = len(edges)
        schema_metadata = registry.snapshot_metadata()
        _add_dependency_edges(nodes_by_id, edges, dataset, project=project)
        resolution_by_dataset[dataset.name], column_resolutions, schema_at_build = (
            _add_lineage_edges(
                nodes_by_id,
                edges,
                warnings,
                dataset,
                project=project,
                dialect=dialect,
                registry=registry,
            )
        )
        registry.register_lineage_outputs(
            dataset.name,
            output_map_keys=resolution_by_dataset[dataset.name].output_map_keys,
            declared_column_types=dict(dataset.column_types),
        )
        model_warnings = tuple(warnings[warning_start:])
        model_edges = frozenset(_normalize_derives_from_edges(edges[edge_start:]))
        resolution_aspect = _derive_model_resolution_aspect(
            dataset=dataset,
            state=resolution_by_dataset[dataset.name],
            edges=model_edges,
            warnings=model_warnings,
            registry=registry,
        )
        _attach_lineage_resolution_aspect(
            nodes_by_id,
            dataset.name,
            resolution_aspect,
        )
        per_model[dataset.name] = ModelLineageResult(
            edges=model_edges,
            warnings=model_warnings,
            schema_at_build=schema_at_build,
            lineage_resolution=resolution_aspect.to_aspect_dict(),
            resolver_status=resolution_aspect.resolver_status,
            column_resolutions=column_resolutions,
            schema_metadata_at_build=schema_metadata,
        )

    for dataset in cyclic_local:
        _add_dependency_edges(nodes_by_id, edges, dataset, project=project)
        cycle_warning = Warning(
            code="dependency_cycle",
            message=(
                f"Dataset {dataset.name!r} participates in a dependency cycle; "
                "lineage resolution skipped."
            ),
            subject_id=table_id(dataset.name),
        )
        warnings.append(cycle_warning)
        resolution_by_dataset[dataset.name] = DatasetResolutionState(
            output_map_keys=set(),
            columns_with_edges=set(),
            columns_with_warnings=set(),
            columns_star_suppressed=set(),
        )
        resolution_aspect = derive_resolver_status(
            ResolverStatusInput(
                edge_count=0,
                declared_column_count=len(dataset.declared_columns),
                output_column_count=0,
                warnings=(cycle_warning,),
                type_status="missing",
                schema_names_available=False,
                schema_types_available=False,
                schema_source="manifest",
                dependency_cycle=True,
            )
        )
        _attach_lineage_resolution_aspect(
            nodes_by_id,
            dataset.name,
            resolution_aspect,
        )
        per_model[dataset.name] = ModelLineageResult(
            edges=frozenset(),
            warnings=(cycle_warning,),
            schema_at_build=registry.to_snapshot(),
            lineage_resolution=resolution_aspect.to_aspect_dict(),
            resolver_status=resolution_aspect.resolver_status,
            column_resolutions=(),
            schema_metadata_at_build=registry.snapshot_metadata(),
        )

    return _TopoLineageBuild(
        resolution_by_dataset=resolution_by_dataset,
        registry=registry,
        per_model=per_model,
        nodes_by_id=nodes_by_id,
        edges=edges,
        warnings=warnings,
    )


def _attach_lineage_resolution_aspect(
    nodes_by_id: dict[str, Node],
    dataset_name: str,
    aspect: LineageResolutionAspect,
) -> None:
    node_id = table_id(dataset_name)
    node = nodes_by_id.get(node_id)
    if node is None:
        return
    aspects = dict(node.aspects or {})
    aspects["lineage_resolution"] = aspect.to_aspect_dict()
    nodes_by_id[node_id] = node.model_copy(update={"aspects": aspects})


def _derive_model_resolution_aspect(
    *,
    dataset: ProjectDataset,
    state: DatasetResolutionState,
    edges: frozenset[tuple[str, str, str, str]],
    warnings: tuple[Warning, ...],
    registry: SchemaRegistry,
) -> LineageResolutionAspect:
    resolved = registry.resolve_relation(dataset.name)
    if isinstance(resolved, RelationSchema):
        column_names = resolved.column_names
        column_types = resolved.column_types
        schema_source = resolved.schema_source
    else:
        column_names = tuple(dataset.declared_columns)
        column_types = dict(dataset.column_types)
        schema_source = "manifest"
    type_status = derive_type_status(
        column_names=column_names or tuple(dataset.declared_columns),
        column_types=column_types or dict(dataset.column_types),
    )
    parse_failed = any(w.code == "lineage_resolution_failed" for w in warnings)
    return derive_resolver_status(
        ResolverStatusInput(
            edge_count=len(edges),
            declared_column_count=len(dataset.declared_columns),
            output_column_count=len(state.output_map_keys),
            warnings=warnings,
            type_status=type_status,
            schema_names_available=bool(column_names or dataset.declared_columns),
            schema_types_available=bool(column_types),
            schema_source=str(schema_source),
            parse_failed=parse_failed,
            no_compiled_sql=not bool(dataset.sql),
        )
    )


def _topological_local_datasets(
    project: ProjectInput,
) -> tuple[list[ProjectDataset], list[ProjectDataset]]:
    local = [
        dataset for dataset in project.datasets.values() if dataset.kind == "local"
    ]
    by_name = {dataset.name: dataset for dataset in local}
    in_degree = {dataset.name: 0 for dataset in local}
    adjacency: dict[str, list[str]] = {dataset.name: [] for dataset in local}

    for dataset in local:
        for dependency_name in dataset.dependency_names:
            if dependency_name not in by_name:
                continue
            adjacency[dependency_name].append(dataset.name)
            in_degree[dataset.name] += 1

    queue = sorted(name for name, degree in in_degree.items() if degree == 0)
    ordered_names: list[str] = []
    while queue:
        current = queue.pop(0)
        ordered_names.append(current)
        for dependent in adjacency[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)
                queue.sort()

    cyclic_names = sorted(name for name, degree in in_degree.items() if degree > 0)
    ordered = [by_name[name] for name in ordered_names]
    cyclic = [by_name[name] for name in cyclic_names]
    return ordered, cyclic


def _emit_type_coverage_warnings(
    warnings: list[Warning],
    dataset: ProjectDataset,
    *,
    dialect: str,
) -> None:
    del dialect
    for column_name in dataset.declared_columns:
        if column_name in dataset.column_types:
            continue
        warnings.append(
            Warning(
                code="missing_column_type",
                message=(f"Column {dataset.name}.{column_name} has no resolved type."),
                subject_id=column_id(dataset.name, column_name),
            )
        )


def _build_lineage(project: ProjectInput, *, dialect: str) -> BuiltLineage:
    topo = _run_topo_lineage(project, dialect=dialect)
    nodes_by_id = topo.nodes_by_id
    edges = topo.edges
    warnings = topo.warnings
    resolution_by_dataset = topo.resolution_by_dataset

    _reconcile_column_coverage(
        warnings,
        project=project,
        resolution_by_dataset=resolution_by_dataset,
    )

    artifact = _stamp_derivation(
        merge(
            CatalogArtifact(
                nodes=sorted(nodes_by_id.values(), key=lambda item: item.id),
                edges=edges,
                warnings=warnings,
            )
        ),
        project=project,
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


def _resolve_lineage_parent_name(
    parent_name: str,
    *,
    project: ProjectInput,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str] = (),
    cte_names: set[str] | frozenset[str] = frozenset(),
    registry: SchemaRegistry | None = None,
) -> str:
    del registry
    from .relations import resolve_sql_visible_table_ref

    return resolve_sql_visible_table_ref(
        parent_name,
        alias_map=alias_map,
        table_references=table_references,
        cte_names=cte_names,
        project=project,
    )


def _parent_is_resolvable(
    parent_name: str,
    *,
    project: ProjectInput,
    alias_map: dict[str, str],
    schema: dict[str, dict[str, str]],
    known_relation_names: set[str],
    table_references: tuple[str, ...] | frozenset[str] = (),
    cte_names: set[str] | frozenset[str] = frozenset(),
    registry: SchemaRegistry | None = None,
) -> bool:
    del schema, known_relation_names
    if registry is None:
        return False
    canonical = _canonical_upstream_relation_id(
        parent_name,
        project=project,
        alias_map=alias_map,
        table_references=table_references,
        cte_names=cte_names,
        registry=registry,
    )
    return canonical is not None


def _add_dataset_node(nodes_by_id: dict[str, Node], dataset: ProjectDataset) -> None:
    dataset_id = table_id(dataset.name)
    if dataset_id in nodes_by_id:
        return
    dbt_aspect = dbt_aspect_for_dataset(dataset)
    aspects = {"dbt": dbt_aspect} if dbt_aspect else None
    nodes_by_id[dataset_id] = Node(
        id=dataset_id,
        kind="table",
        name=leaf_name(dataset.name),
        qualified_name=dataset.name,
        schema=schema_name(dataset.name),
        evidence=_dataset_evidence(dataset),
        aspects=aspects,
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
    registry: SchemaRegistry,
) -> tuple[
    DatasetResolutionState,
    tuple[ColumnLineageResolution, ...],
    dict[str, dict[str, str]],
]:
    state = DatasetResolutionState(
        output_map_keys=set(),
        columns_with_edges=set(),
        columns_with_warnings=set(),
        columns_star_suppressed=set(),
    )
    parse_failed = False
    try:
        statement_analysis = analyze_sql_statement(dataset.sql or "", dialect=dialect)
    except LineageInputError as exc:
        statement_analysis = None
        parse_failed = True
        _emit_column_warning(
            warnings,
            code="lineage_resolution_failed",
            dataset=dataset,
            column_name=None,
            message=f"SQL analysis failed for dataset {dataset.name!r}: {exc}",
        )
    lineage_schema: dict[str, dict[str, str]] = {}
    if statement_analysis is None and not parse_failed:
        schema_snapshot = registry.lineage_schema_for_model(dataset, project)
        lineage_schema = schema_snapshot.schema
        known_relation_names: set[str] = set()
        alias_map: dict[str, str] = {}
        cte_name_set: set[str] = set()
        table_references: tuple[str, ...] = ()
        aliased_table_star = False
        star_policy = None
        quoted_outputs: frozenset[str] = frozenset()
        has_union = False
        has_select_star = False
    elif statement_analysis is None:
        return state, (), {}
    else:
        known_relation_names = {
            normalize_identifier(reference)
            for reference in statement_analysis.table_references
        }
        alias_map = statement_analysis.alias_map
        cte_name_set = statement_analysis.cte_names
        table_references = statement_analysis.table_references
        schema_snapshot = registry.lineage_schema_for_model(
            dataset,
            project,
            table_references=table_references,
            alias_map=alias_map,
            cte_names=cte_name_set,
        )
        lineage_schema = schema_snapshot.schema
        for unresolved in schema_snapshot.unresolved:
            relation_id = (
                unresolved.relation_id
                if isinstance(unresolved, MissingSchema)
                else unresolved.reference
            )
            _emit_column_warning(
                warnings,
                code="schema_missing",
                dataset=dataset,
                column_name=None,
                message=(
                    f"Schema unavailable for relation {relation_id!r} "
                    f"referenced in SQL for dataset {dataset.name!r}: "
                    f"{unresolved.reason}"
                ),
            )
        aliased_table_star = uses_outer_aliased_table_star(statement_analysis)
        has_select_star = has_select_star_projection(statement_analysis)
        star_policy = (
            star_expansion_policy(
                statement_analysis,
                schema=lineage_schema,
                datasets=project.datasets,
            )
            if has_select_star
            else None
        )
        quoted_outputs = quoted_alias_output_columns(statement_analysis)
        has_union = statement_analysis.has_union
    try:
        output_map = lineage_output_map(
            dataset.sql or "",
            schema=lineage_schema,
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
        return state, (), dict(lineage_schema)

    if not isinstance(output_map, dict):
        raise LineageContractError(
            "clearmetric-core expected lineage_output_map(...) to return a dict."
        )

    fallback_map: dict[str, SqlglotLineageNode] = {}
    if (
        has_union
        and statement_analysis is not None
        and is_macro_generated_union(statement_analysis)
    ):
        qualified_keys = {
            normalize_identifier_part(name) for name in output_map if name != "*"
        }
        try:
            fallback_map = lineage_output_map(
                dataset.sql or "",
                schema=lineage_schema,
                sources=None,
                dialect=dialect,
            )
        except LineageInputError:
            fallback_map = {}
        for column, fallback_root in fallback_map.items():
            if column == "*" or column in output_map:
                continue
            if normalize_identifier_part(column) in qualified_keys:
                continue
            output_map[column] = fallback_root

    unqualified_lineage_map: dict[str, SqlglotLineageNode] | None = None
    macro_union_schema_refs_index: dict[str, set[str]] | None = None
    if has_union and statement_analysis is not None:
        if fallback_map:
            unqualified_lineage_map = fallback_map
        else:
            try:
                unqualified_lineage_map = lineage_output_map(
                    dataset.sql or "",
                    schema=lineage_schema,
                    sources=None,
                    dialect=dialect,
                )
            except LineageInputError:
                unqualified_lineage_map = {}
        if is_macro_generated_union(statement_analysis):

            def _resolve_canonical_parent(base_relation: str) -> str | None:
                return _canonical_upstream_relation_id(
                    base_relation,
                    project=project,
                    alias_map=alias_map,
                    table_references=table_references,
                    cte_names=cte_name_set,
                    registry=registry,
                )

            allowed = _union_allowed_parent_ids(
                dataset,
                table_references=table_references,
                project=project,
            )
            macro_union_schema_refs_index = macro_union_schema_branch_refs_index(
                analysis=statement_analysis,
                registry=registry,
                project=project,
                resolve_canonical_parent=_resolve_canonical_parent,
                parent_is_allowed=lambda parent: _parent_allowed_for_union(
                    parent, allowed
                ),
            )

    column_resolutions: list[ColumnLineageResolution] = []
    for output_name, root in sorted(output_map.items(), key=lambda item: item[0]):
        if output_name == "*":
            expanded_any = False
            for declared_output in dataset.declared_columns:
                resolution = _registry_proven_star_resolution(
                    output_name=declared_output,
                    dataset=dataset,
                    project=project,
                    lineage_schema=lineage_schema,
                    statement_analysis=statement_analysis,
                    registry=registry,
                )
                if resolution is None:
                    continue
                normalized_declared = normalize_identifier_part(declared_output)
                state.output_map_keys.add(normalized_declared)
                _add_column_node(
                    nodes_by_id,
                    dataset.name,
                    declared_output,
                    dataset.evidence_file,
                )
                column_resolutions.append(resolution)
                _apply_column_lineage_resolution(
                    resolution,
                    nodes_by_id=nodes_by_id,
                    edges=edges,
                    warnings=warnings,
                    dataset=dataset,
                    project=project,
                    state=state,
                    alias_map=alias_map,
                    lineage_schema=lineage_schema,
                    known_relation_names=known_relation_names,
                    registry=registry,
                )
                expanded_any = True
            if expanded_any:
                continue
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
        resolution = _resolve_output_column_lineage(
            output_name=output_name,
            root=root,
            dataset=dataset,
            project=project,
            dialect=dialect,
            lineage_schema=lineage_schema,
            alias_map=alias_map,
            cte_name_set=cte_name_set,
            table_references=table_references,
            known_relation_names=known_relation_names,
            has_union=has_union,
            has_select_star=has_select_star,
            star_policy=star_policy,
            quoted_outputs=quoted_outputs,
            aliased_table_star=aliased_table_star,
            statement_analysis=statement_analysis,
            registry=registry,
            unqualified_lineage_map=unqualified_lineage_map,
            macro_union_schema_refs_index=macro_union_schema_refs_index,
        )
        column_resolutions.append(resolution)
        _apply_column_lineage_resolution(
            resolution,
            nodes_by_id=nodes_by_id,
            edges=edges,
            warnings=warnings,
            dataset=dataset,
            project=project,
            state=state,
            alias_map=alias_map,
            lineage_schema=lineage_schema,
            known_relation_names=known_relation_names,
            registry=registry,
        )
    if has_select_star:
        _emit_select_star_outcome_warning(
            warnings,
            dataset=dataset,
            state=state,
            star_policy=star_policy,
        )
    schema_at_build = dict(lineage_schema)
    return state, tuple(column_resolutions), schema_at_build


def _extract_cte_context(
    refs: set[str],
    *,
    cte_names: set[str],
    alias_map: dict[str, str],
) -> tuple[tuple[str, ...], frozenset[tuple[str, str]]]:
    chain: list[str] = []
    aliases: set[tuple[str, str]] = set()
    for ref in refs:
        parsed = _try_split_ref(ref)
        if parsed is None:
            continue
        parent_name, _column_name = parsed
        parent_key = normalize_identifier_part(parent_name)
        if parent_key in cte_names:
            chain.append(parent_key)
        if parent_key in alias_map:
            aliases.add((parent_key, alias_map[parent_key]))
    return tuple(dict.fromkeys(chain)), frozenset(aliases)


def _registry_proven_star_resolution(
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


def _refs_target_outer_base_relations(
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
        parsed = _try_split_ref(ref)
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


def _refs_resolve_to_project_datasets(
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
        parsed = _try_split_ref(ref)
        if parsed is None:
            return False
        parent_name, _column_name = parsed
        try:
            resolved = _resolve_lineage_parent_name(
                parent_name,
                project=project,
                alias_map=alias_map,
                table_references=table_references,
                cte_names=cte_names,
                registry=registry,
            )
        except LineageInputError:
            return False
        if resolved not in project.datasets:
            return False
    return True


def _outer_from_is_cte_only(statement_analysis) -> bool:
    """True when the outer FROM/JOIN lists only CTE relations (no base tables)."""
    relations = from_clause_base_relations(statement_analysis)
    if not relations:
        return False
    return all(relation in statement_analysis.cte_names for relation in relations)


def _canonical_upstream_relation_id(
    parent_name: str,
    *,
    project: ProjectInput,
    alias_map: dict[str, str],
    table_references: tuple[str, ...] | frozenset[str],
    cte_names: set[str] | frozenset[str],
    registry: SchemaRegistry,
) -> str | None:
    try:
        resolved = _resolve_lineage_parent_name(
            parent_name,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_names,
            registry=registry,
        )
    except LineageInputError:
        return None
    outcome = registry.resolve_relation(resolved, alias_map=alias_map)
    if isinstance(outcome, (RelationSchema, MissingSchema)):
        return outcome.relation_id
    resolved_key = normalize_identifier_part(resolved)
    if resolved in cte_names or resolved_key in cte_names:
        return normalize_identifier(resolved_key)

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


def _resolved_ref_parent_names(
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
        parsed = _try_split_ref(ref)
        if parsed is None:
            continue
        parent_name, _column_name = parsed
        canonical = _canonical_upstream_relation_id(
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


def _dependency_closure_relation_names(
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


def _union_allowed_parent_ids(
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


def _parent_allowed_for_union(parent_id: str, allowed_ids: set[str]) -> bool:
    from .relations import relation_fqn_lookup_keys

    keys = {normalize_identifier(parent_id)}
    keys.update(relation_fqn_lookup_keys(parent_id))
    return bool(keys & allowed_ids)


def _macro_union_branch_parents_safe(
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
        if not _parent_allowed_for_union(parent, allowed):
            return False
    return True


def _union_refs_are_safe_to_resolve(
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
    if _refs_target_outer_base_relations(
        refs,
        statement_analysis=statement_analysis,
        alias_map=alias_map,
    ):
        return True
    allowed = _union_allowed_parent_ids(
        dataset,
        table_references=table_references,
        project=project,
    )
    resolved_parents = _resolved_ref_parent_names(
        refs,
        project=project,
        alias_map=alias_map,
        table_references=table_references,
        cte_names=cte_names,
        registry=registry,
    )
    if isinstance(statement_analysis.statement, exp.Union):
        if union_has_null_padding_asymmetry(statement_analysis) and resolved_parents and all(
            parent in project.datasets
            and _parent_allowed_for_union(parent, allowed)
            for parent in resolved_parents
        ):
            return True
    if not isinstance(statement_analysis.statement, exp.Union) and _macro_union_branch_parents_safe(
        resolved_parents,
        allowed=allowed,
        project=project,
    ):
        return True
    if not _outer_from_is_cte_only(statement_analysis):
        return False
    if not _refs_resolve_to_project_datasets(
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
        if not _parent_allowed_for_union(parent, allowed):
            return False
    return True


def _expand_union_branch_cte_refs(
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
        parsed = _try_split_ref(ref)
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
        canonical = _canonical_upstream_relation_id(
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


def _resolvable_union_branch_refs(
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
    expanded = _expand_union_branch_cte_refs(
        refs,
        statement_analysis=statement_analysis,
        project=project,
        alias_map=alias_map,
        table_references=table_references,
        cte_names=cte_names,
        registry=registry,
    )
    allowed = _union_allowed_parent_ids(
        dataset,
        table_references=table_references,
        project=project,
    )
    resolvable: set[str] = set()
    for ref in expanded:
        parsed = _try_split_ref(ref)
        if parsed is None:
            continue
        parent_name, column_name = parsed
        canonical = _canonical_upstream_relation_id(
            parent_name,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_names,
            registry=registry,
        )
        if canonical is None or canonical not in project.datasets:
            continue
        if not _parent_allowed_for_union(canonical, allowed):
            continue
        resolvable.add(normalize_identifier(f"{canonical}.{column_name}"))
    return resolvable


def _collect_union_branch_upstream_refs(
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
    sqlglot_branch_refs = _collect_union_branch_refs(
        output_name=output_name,
        dataset=dataset,
        project=project,
        alias_map=alias_map,
        cte_names=cte_names,
        known_relation_names=known_relation_names,
        lineage_schema=lineage_schema,
        dialect=dialect,
        unqualified_lineage_map=unqualified_lineage_map,
    )

    def resolve_canonical_parent(base_relation: str) -> str | None:
        return _canonical_upstream_relation_id(
            base_relation,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_names,
            registry=registry,
        )

    allowed = _union_allowed_parent_ids(
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
        parent_is_allowed=lambda parent: _parent_allowed_for_union(parent, allowed),
        macro_union_schema_refs_index=macro_union_schema_refs_index,
    )


def _collect_union_branch_refs(
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
) -> set[str]:
    """Collect per-branch upstream refs for UNION outputs using unqualified lineage."""
    sql = dataset.sql
    if not sql:
        return set()
    if unqualified_lineage_map is not None:
        unqualified_root = unqualified_lineage_map.get(output_name)
    else:
        try:
            unqualified_root = lineage_output_map(
                sql,
                schema=lineage_schema,
                sources=None,
                dialect=dialect,
            ).get(output_name)
        except LineageInputError:
            return set()
    if unqualified_root is None:
        return set()
    union_refs: set[str] = set()
    for child in unqualified_root.downstream:
        union_refs.update(
            _refs_from_lineage_subtree(
                child,
                project=project,
                dataset=dataset,
                alias_map=alias_map,
                cte_names=set(cte_names),
                schema=lineage_schema,
                preserve_cte_scope=True,
            )
        )
    return _remap_root_sources_to_local_deps(
        union_refs,
        project=project,
        dataset=dataset,
        known_relation_names=known_relation_names,
        schema=lineage_schema,
    )


def _resolve_output_column_lineage(
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
        _shadowed_outer_select_aliases(statement_analysis)
        if statement_analysis is not None
        else {}
    )
    if has_select_star and is_star_suppressed_output(
        output_name,
        star_policy,
        statement_analysis=statement_analysis,
        lineage_root=root,
    ):
        allowed_relations = _dependency_closure_relation_names(dataset, project)
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
            _single_bare_star_source_relation(statement_analysis)
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
    if has_union:
        union_branch_refs = _collect_union_branch_upstream_refs(
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
    immediate_refs = _collect_immediate_upstream_refs(
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
        resolvable_union = _resolvable_union_branch_refs(
            union_branch_refs,
            statement_analysis=statement_analysis,
            project=project,
            dataset=dataset,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_name_set,
            registry=registry,
        )
        if resolvable_union and _union_refs_are_safe_to_resolve(
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
            for ref in _collect_all_refs(root)
            if ref != output_name and ref != f"{dataset.name}.{output_name}"
        }
        local_refs = {
            ref
            for ref in all_refs
            if _is_local_ref(ref, project=project, current_dataset=dataset.name)
        }
        if local_refs:
            selected_refs = local_refs
            ref_strategy = "local_refs"
        else:
            selected_refs = _collect_leaf_refs(root)
            ref_strategy = "leaf_refs"
    if selected_refs == {"*"} and not has_select_star:
        expanded_refs = {
            ref
            for ref in _collect_all_refs(root)
            if ref != "*"
            and ref != output_name
            and ref != f"{dataset.name}.{output_name}"
            and _try_split_ref(ref) is not None
        }
        if expanded_refs:
            selected_refs = expanded_refs
            ref_strategy = "expanded_star"

    pre_filter_refs = frozenset(selected_refs)
    cte_chain, cte_local_aliases = _extract_cte_context(
        set(selected_refs),
        cte_names=cte_name_set,
        alias_map=alias_map,
    )

    if (
        has_union
        and ref_strategy != "union_branches"
        and not _union_refs_are_safe_to_resolve(
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
                if (parsed := _try_split_ref(ref)) is not None
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

    post_filter_refs = frozenset(selected_refs)

    if (
        aliased_table_star
        and selected_refs
        and _refs_target_only_root_datasets(selected_refs, project=project)
        and _refs_confined_to_qualified_star_aliases(
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
        registry_resolution = _registry_proven_star_resolution(
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
        parsed_ref = _try_split_ref(leaf_ref)
        if parsed_ref is None:
            continue
        parent_name, source_column = parsed_ref
        if not _parent_is_resolvable(
            parent_name,
            project=project,
            alias_map=alias_map,
            schema=lineage_schema,
            known_relation_names=known_relation_names,
            table_references=table_references,
            cte_names=cte_name_set,
            registry=registry,
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
                    f"Lineage resolved relation alias {parent_name!r} instead of a "
                    "concrete upstream dataset for output column "
                    f"{dataset.name}.{output_name}."
                ),
            )
        canonical_parent = _canonical_upstream_relation_id(
            parent_name,
            project=project,
            alias_map=alias_map,
            table_references=table_references,
            cte_names=cte_name_set,
            registry=registry,
        )
        if canonical_parent is None:
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
                    f"Unable to resolve upstream relation for output column "
                    f"{dataset.name}.{output_name} from parent {parent_name!r}."
                ),
            )
        cte_key = normalize_identifier_part(canonical_parent)
        if (
            cte_key in cte_name_set
            and statement_analysis is not None
            and cte_projected_column_is_literal(
                cte_key,
                source_column,
                analysis=statement_analysis,
            )
        ):
            continue
        mapped.add(
            (canonical_parent, source_column, dataset.name, output_name),
        )

    if (
        not mapped
        and statement_analysis is not None
        and root.expression is not None
    ):
        outer = _outer_select(statement_analysis.statement)
        expression = _unwrap_alias(root.expression)
        if expression is not None and not isinstance(expression, exp.Column):
            for relation, source_column in expression_column_refs(
                expression,
                analysis=statement_analysis,
                select=outer if isinstance(outer, exp.Select) else None,
            ):
                if not _parent_is_resolvable(
                    relation,
                    project=project,
                    alias_map=alias_map,
                    schema=lineage_schema,
                    known_relation_names=known_relation_names,
                    table_references=table_references,
                    cte_names=cte_name_set,
                    registry=registry,
                ):
                    continue
                canonical_parent = _canonical_upstream_relation_id(
                    relation,
                    project=project,
                    alias_map=alias_map,
                    table_references=table_references,
                    cte_names=cte_name_set,
                    registry=registry,
                )
                if canonical_parent is None:
                    continue
                cte_key = normalize_identifier_part(canonical_parent)
                if (
                    cte_key in cte_name_set
                    and statement_analysis is not None
                    and cte_projected_column_is_literal(
                        cte_key,
                        source_column,
                        analysis=statement_analysis,
                    )
                ):
                    continue
                mapped.add(
                    (canonical_parent, source_column, dataset.name, output_name),
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


def _apply_column_lineage_resolution(
    resolution: ColumnLineageResolution,
    *,
    nodes_by_id: dict[str, Node],
    edges: list[Edge],
    warnings: list[Warning],
    dataset: ProjectDataset,
    project: ProjectInput,
    state: DatasetResolutionState,
    alias_map: dict[str, str],
    lineage_schema: dict[str, dict[str, str]],
    known_relation_names: set[str],
    registry: SchemaRegistry,
) -> None:
    del alias_map, lineage_schema, known_relation_names, registry
    normalized_output = normalize_identifier_part(resolution.output_column)
    if resolution.star_suppressed:
        state.columns_star_suppressed.add(normalized_output)
    if resolution.warning_code == "unresolved_lineage":
        _emit_column_warning(
            warnings,
            code=resolution.warning_code,
            dataset=dataset,
            column_name=resolution.output_column,
            message=(
                "Quoted output identifier declined for value-lineage edge emission on "
                f"{dataset.name}.{resolution.output_column}."
            ),
            state=state,
        )
        return
    if resolution.warning_code is not None and not resolution.mapped_edges:
        messages = {
            "unresolved_star_source": (
                "Value-lineage filtering removed all upstream refs and only '*' remained "
                f"for {dataset.name}.{resolution.output_column}."
                if resolution.post_filter_refs == frozenset()
                else (
                    "Lineage leaf expansion stayed at '*' for output column "
                    f"{dataset.name}.{resolution.output_column}."
                )
            ),
            "unresolved_output_source": (
                "Value-lineage filtering removed all upstream refs for output column "
                f"{dataset.name}.{resolution.output_column}."
                if not resolution.post_filter_refs and resolution.pre_filter_refs
                else (
                    "Lineage resolved no upstream value leaves for output column "
                    f"{dataset.name}.{resolution.output_column}."
                )
            ),
        }
        _emit_column_warning(
            warnings,
            code=resolution.warning_code,
            dataset=dataset,
            column_name=resolution.output_column,
            message=resolution.warning_message
            or messages.get(
                resolution.warning_code,
                f"Lineage resolution failed for {dataset.name}.{resolution.output_column}.",
            ),
            state=state,
        )
        return

    source_id = column_id(dataset.name, resolution.output_column)
    for upstream_table, upstream_col, _dest_table, _dest_col in resolution.mapped_edges:
        parent_dataset = project.datasets.get(upstream_table)
        _add_dataset_node(
            nodes_by_id,
            ProjectDataset(
                name=upstream_table,
                kind="root" if parent_dataset is None else parent_dataset.kind,
                sql=parent_dataset.sql if parent_dataset else None,
                dependency_names=(),
                declared_columns=parent_dataset.declared_columns
                if parent_dataset
                else (),
                evidence_file=parent_dataset.evidence_file if parent_dataset else None,
            ),
        )
        _add_column_node(nodes_by_id, upstream_table, upstream_col, None)
        expression = f"{upstream_table}.{upstream_col}"
        confidence: Confidence = (
            "high" if resolution.ref_selection_strategy == "immediate" else "medium"
        )
        edges.append(
            Edge(
                kind="derives_from",
                source_id=source_id,
                target_id=column_id(upstream_table, upstream_col),
                label="derives_from",
                evidence=[
                    Evidence(
                        file=dataset.evidence_file,
                        expression=expression,
                        confidence=confidence,
                    )
                ]
                if dataset.evidence_file
                else [],
            )
        )
        state.columns_with_edges.add(normalized_output)


def _emit_select_star_outcome_warning(
    warnings: list[Warning],
    *,
    dataset: ProjectDataset,
    state: DatasetResolutionState,
    star_policy: StarExpansionPolicy | None,
) -> None:
    expanded_successfully = (
        star_policy is None
        and bool(state.columns_with_edges)
        and not state.columns_star_suppressed
    )
    has_unresolved_star = any(
        warning.code == "unresolved_star_source"
        and warning.location
        and (
            warning.location == f"{dataset.name}.*"
            or warning.location.startswith(f"{dataset.name}.")
        )
        for warning in warnings
    )
    if expanded_successfully and not has_unresolved_star:
        _emit_column_warning(
            warnings,
            code=EXPANDED_STAR_CODE,
            dataset=dataset,
            column_name=None,
            message=(
                f"SELECT * was expanded from declared upstream columns for "
                f"{dataset.name!r}."
            ),
        )
        return
    if not any(
        warning.code == "select_star"
        and warning.subject_id is None
        and warning.location == f"{dataset.name}.*"
        for warning in warnings
    ):
        _emit_column_warning(
            warnings,
            code="select_star",
            dataset=dataset,
            column_name=None,
            message="SELECT * was detected; output mapping may stay warning-rich.",
        )


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
    shadowed_aliases: dict[str, str],
    cte_names: set[str],
    known_relation_names: set[str],
    schema: dict[str, dict[str, str]],
) -> set[str]:
    refs: set[str] = set()
    for child in root.downstream:
        refs.update(
            _refs_from_lineage_subtree(
                child,
                project=project,
                dataset=dataset,
                alias_map=alias_map,
                shadowed_aliases=shadowed_aliases,
                cte_names=cte_names,
                schema=schema,
            )
        )
    return _remap_root_sources_to_local_deps(
        refs,
        project=project,
        dataset=dataset,
        known_relation_names=known_relation_names,
        schema=schema,
    )


def _downstream_is_star_only(node: SqlglotLineageNode) -> bool:
    if not node.downstream:
        return True
    for child in node.downstream:
        if child.name.strip() == "*":
            continue
        if _try_split_ref(child.name) is not None:
            return False
        if not _downstream_is_star_only(child):
            return False
    return True


def _refs_from_lineage_subtree(
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
    parsed = _try_split_ref(node.name)
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
        if parent_key in cte_names or _is_derived_scope_name(
            parent_key,
            project=project,
            alias_map=alias_map,
            cte_names=cte_names,
            schema=schema or {},
        ):
            scoped_refs: set[str] = set()
            for child in node.downstream:
                scoped_refs.update(
                    _refs_from_lineage_subtree(
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
                not node.downstream or _downstream_is_star_only(node)
            ):
                return {normalize_identifier(f"{parent_key}.{column_name}")}
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
    schema: dict[str, dict[str, str]],
) -> set[str]:
    remapped: set[str] = set()
    for ref in refs:
        parsed = _try_split_ref(ref)
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
    schema: dict[str, dict[str, str]],
) -> bool:
    if parent_key in cte_names:
        return True
    if parent_key in project.datasets or parent_key in schema:
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


def _refs_confined_to_qualified_star_aliases(
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
        parsed = _try_split_ref(ref)
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


def _collect_leaf_refs(
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
        refs.update(_collect_leaf_refs(child, _seen=_seen))
    return refs


def _collect_all_refs(
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
        refs.update(_collect_all_refs(child, _seen=_seen))
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


def _stamp_derivation(
    artifact: CatalogArtifact,
    *,
    project: ProjectInput,
) -> CatalogArtifact:
    source = "dbt_manifest" if project.input_kind == "dbt_manifest" else "sqlglot"
    warning_codes_by_subject: dict[str, set[str]] = {}
    for warning in artifact.warnings:
        if warning.subject_id:
            warning_codes_by_subject.setdefault(warning.subject_id, set()).add(
                warning.code
            )
        if warning.location:
            dataset = dataset_from_location(warning.location)
            if dataset:
                warning_codes_by_subject.setdefault(f"table:{dataset}", set()).add(
                    warning.code
                )

    def _codes_for(subject_id: str | None) -> set[str]:
        if not subject_id:
            return set()
        codes = set(warning_codes_by_subject.get(subject_id, set()))
        if subject_id.startswith("column:"):
            parent = subject_id.removeprefix("column:").rsplit(".", 1)[0]
            codes |= warning_codes_by_subject.get(f"table:{parent}", set())
        return codes

    def _status_for(
        subject_id: str | None,
        *,
        default: DerivationStatus = "complete",
    ) -> tuple[DerivationStatus, Confidence]:
        if not subject_id:
            return default, "high"
        codes = _codes_for(subject_id)
        if any(
            code.endswith("_failed") or code == "lineage_resolution_failed"
            for code in codes
        ):
            return "failed", "low"
        material = codes - {EXPANDED_STAR_CODE}
        if material:
            return "partial", "medium"
        return default, "high"

    stamped_nodes: list[Node] = []
    for node in artifact.nodes:
        status, confidence = _status_for(node.id)
        stamped_nodes.append(
            node.model_copy(
                update={
                    "derivation": DerivationState(
                        status=status,
                        confidence=confidence,
                        source=source,
                    )
                }
            )
        )

    stamped_edges: list[Edge] = []
    for edge in artifact.edges:
        status, confidence = _status_for(edge.source_id, default="complete")
        stamped_edges.append(
            edge.model_copy(
                update={
                    "derivation": DerivationState(
                        status=status,
                        confidence=confidence,
                        source=source,
                    )
                }
            )
        )

    return artifact.model_copy(update={"nodes": stamped_nodes, "edges": stamped_edges})
