"""Artifact assembly for clearmetric-core."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml
from clearmetric.core import (
    CatalogArtifact,
    DerivationState,
    Edge,
    Evidence,
    Node,
    Warning,
    column_id,
    leaf_name,
    load_artifact_file,
    merge,
    normalize_identifier,
    normalize_identifier_part,
    render_json,
    schema_name,
    table_id,
)
from clearmetric.core.models import Confidence, DerivationStatus
from clearmetric.graph import dataset_from_location
from sqlglot.lineage import Node as SqlglotLineageNode

from .coverage import EXPANDED_STAR_CODE
from .errors import LineageContractError, LineageInputError
from .loaders import (
    ProjectDataset,
    ProjectInput,
    dbt_aspect_for_dataset,
    subset_project,
)
from .models import LineageMap, LineageSummary
from .output_columns import infer_output_columns
from .column_resolution import (
    ColumnLineageResolution,
    build_unqualified_lineage_map,
    macro_union_schema_refs_index_for_dataset,
    merge_macro_union_output_columns,
    registry_proven_star_resolution,
    resolve_output_column_lineage,
)
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
    analyze_sql_statement,
    has_select_star_projection,
    lineage_output_map,
    quoted_alias_output_columns,
    star_expansion_policy,
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
    progress: Callable[[int, int, str], None] | None = None,
) -> CatalogArtifact:
    return _build_lineage(project, dialect=dialect, progress=progress).artifact


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


def edges_by_model_from_artifact(
    artifact: CatalogArtifact,
) -> dict[str, frozenset[tuple[str, str, str, str]]]:
    """Group normalized derives_from edges by downstream model name."""
    per_model: dict[str, set[tuple[str, str, str, str]]] = {}
    for edge in _normalize_derives_from_edges(artifact.edges):
        per_model.setdefault(edge[2], set()).add(edge)
    return {model: frozenset(edges) for model, edges in per_model.items()}


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lineage_engine_fingerprint(*, input_paths: Sequence[Path] = ()) -> str:
    """Hash engine modules plus optional caller-supplied input paths."""
    from . import sql_analyzer as sql_analyzer_module

    parts: list[str] = []
    for module_path in (__file__, sql_analyzer_module.__file__):
        if module_path is None:
            raise LineageContractError("Unable to resolve engine module path.")
        parts.append(_file_sha256(Path(module_path)))
    for path in input_paths:
        parts.append(_file_sha256(path.expanduser().resolve()))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def default_lineage_cache_dir() -> Path:
    env = os.environ.get("CLEARMETRIC_LINEAGE_CACHE_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return Path("_lineage_build_cache")


def _scoped_cache_paths(
    cache_dir: Path,
    *,
    fingerprint: str,
    scope_models: frozenset[str],
) -> tuple[str, Path, Path]:
    scope_key = "|".join(sorted(scope_models))
    cache_key = hashlib.sha256(
        f"{fingerprint}|{scope_key}".encode("utf-8")
    ).hexdigest()
    return (
        cache_key,
        cache_dir / f"{cache_key}.json",
        cache_dir / f"{cache_key}.meta.yaml",
    )


def build_scoped_lineage_cached(
    project: ProjectInput,
    scope_models: frozenset[str],
    cache_dir: Path,
    *,
    dialect: str,
    fingerprint: str,
    force: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[CatalogArtifact, dict[str, frozenset[tuple[str, str, str, str]]]]:
    """Build or load a scoped CatalogArtifact keyed by fingerprint + scope."""
    cache_key, artifact_path, meta_path = _scoped_cache_paths(
        cache_dir,
        fingerprint=fingerprint,
        scope_models=scope_models,
    )
    if meta_path.is_file() and artifact_path.is_file() and not force:
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise LineageContractError(f"Invalid lineage cache meta: {meta_path}")
        if meta.get("fingerprint") != fingerprint:
            raise LineageContractError(
                f"Lineage cache stale for fingerprint {fingerprint!r}: {meta_path}. "
                "Rerun with --force."
            )
        if frozenset(meta.get("scope_models") or []) != scope_models:
            raise LineageContractError(
                f"Lineage cache scope mismatch: {meta_path}. Rerun with --force."
            )
        artifact = load_artifact_file(artifact_path)
        return artifact, edges_by_model_from_artifact(artifact)

    scoped_project = subset_project(project, scope_models)
    artifact = build_catalog_artifact_from_project(
        scoped_project,
        dialect=dialect,
        progress=progress,
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(render_json(artifact), indent=2, sort_keys=False),
        encoding="utf-8",
    )
    meta = {
        "fingerprint": fingerprint,
        "cache_key": cache_key,
        "scope_models": sorted(scope_models),
        "build_scope_local_models": len(scope_models),
        "written_at": datetime.now(tz=UTC).isoformat(),
    }
    meta_path.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    return artifact, edges_by_model_from_artifact(artifact)


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


def _build_lineage(
    project: ProjectInput,
    *,
    dialect: str,
    progress: Callable[[int, int, str], None] | None = None,
) -> BuiltLineage:
    topo = _run_topo_lineage(project, dialect=dialect, progress=progress)
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
    except (LineageInputError, LineageContractError) as exc:
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

    unqualified_lineage_map: dict[str, SqlglotLineageNode] | None = None
    macro_union_schema_refs_index: dict[str, set[str]] | None = None
    if has_union and statement_analysis is not None:
        try:
            unqualified_lineage_map = build_unqualified_lineage_map(
                dataset.sql or "",
                schema=lineage_schema,
                dialect=dialect,
            )
        except LineageInputError as exc:
            _emit_column_warning(
                warnings,
                code="lineage_resolution_failed",
                dataset=dataset,
                column_name=None,
                message=(
                    f"Unqualified lineage map failed for dataset {dataset.name!r}: {exc}"
                ),
            )
        if unqualified_lineage_map:
            merge_macro_union_output_columns(
                output_map,
                unqualified_lineage_map=unqualified_lineage_map,
            )
            macro_union_schema_refs_index = macro_union_schema_refs_index_for_dataset(
                dataset=dataset,
                project=project,
                analysis=statement_analysis,
                registry=registry,
                alias_map=alias_map,
                table_references=table_references,
                cte_names=cte_name_set,
            )

    column_resolutions: list[ColumnLineageResolution] = []
    for output_name, root in sorted(output_map.items(), key=lambda item: item[0]):
        if output_name == "*":
            expanded_any = False
            for declared_output in dataset.declared_columns:
                resolution = registry_proven_star_resolution(
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
        resolution = resolve_output_column_lineage(
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
