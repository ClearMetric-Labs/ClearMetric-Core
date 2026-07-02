"""Project input loaders for clearmetric-core."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict, cast

from clearmetric.core import leaf_name, normalize_identifier
from clearmetric.core.models import Warning

from .errors import LineageInputError
from .relations import relation_fqn_lookup_keys
from .schema import (
    ResolverTypeOverlay,
    TypeSource,
    _TypedDataset,
    overlay_types_from_dbt_catalog,
    resolve_dataset_column_types,
    to_sqlglot_schema,
)
from .sql_analyzer import list_table_references

ProjectDatasetKind = Literal["local", "root"]
InputKind = Literal["dbt_manifest", "sql_folder"]


class DbtDatasetMetadata(TypedDict):
    unique_id: str
    package_name: str | None
    manifest_name: str | None
    alias: str | None
    database: str | None
    schema_name: str | None
    relation_name: str | None
    resource_type: str | None


@dataclass(frozen=True)
class ProjectDataset:
    name: str
    kind: ProjectDatasetKind
    sql: str | None
    dependency_names: tuple[str, ...]
    declared_columns: tuple[str, ...]
    evidence_file: str | None
    column_types: dict[str, str] = field(default_factory=dict)
    type_sources: dict[str, TypeSource] = field(default_factory=dict)
    unique_id: str | None = None
    package_name: str | None = None
    manifest_name: str | None = None
    alias: str | None = None
    database: str | None = None
    schema_name: str | None = None
    relation_name: str | None = None
    resource_type: str | None = None


@dataclass(frozen=True)
class ManifestCompileReport:
    models_total: int
    models_with_compiled_sql: int
    models_missing_compiled_sql: tuple[str, ...]


@dataclass(frozen=True)
class ProjectInput:
    input_kind: InputKind
    label: str
    datasets: dict[str, ProjectDataset]
    manifest_compile_report: ManifestCompileReport | None = None
    type_warnings: tuple[Warning, ...] = field(default_factory=tuple)

    def local_dataset_names(self) -> set[str]:
        return {
            dataset.name
            for dataset in self.datasets.values()
            if dataset.kind == "local"
        }

    def typed_schema(
        self,
        *,
        include_local: set[str] | None = None,
        accumulated: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, dict[str, str]]:
        """Return sqlglot schema with resolved types only."""
        base = to_sqlglot_schema(
            cast(Mapping[str, _TypedDataset], self.datasets),
            include_local=include_local,
        )
        if accumulated is None:
            return base
        merged = dict(accumulated)
        for name, columns in base.items():
            merged.setdefault(name, {}).update(columns)
        return merged

    def sources_for(self, dataset_name: str) -> dict[str, str]:
        local_names = self.local_dataset_names()
        visited: set[str] = set()
        stack = list(self.datasets[dataset_name].dependency_names)
        while stack:
            dependency_name = stack.pop()
            if dependency_name not in local_names or dependency_name in visited:
                continue
            visited.add(dependency_name)
            stack.extend(self.datasets[dependency_name].dependency_names)
        return {
            dependency_name: self.datasets[dependency_name].sql or ""
            for dependency_name in sorted(visited)
        }


def load_project(
    path: str | Path,
    *,
    dialect: str,
    warehouse_overlay: ResolverTypeOverlay | None = None,
    strict_types: bool = False,
) -> ProjectInput:
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise LineageInputError(f"Project input does not exist: {target}")
    if target.is_file():
        if target.name != "manifest.json":
            raise LineageInputError(
                "clearmetric-core file input must be a dbt manifest.json."
            )
        return _load_manifest_project(
            target,
            dialect=dialect,
            warehouse_overlay=warehouse_overlay,
            strict_types=strict_types,
        )
    if target.is_dir():
        project = _load_sql_folder_project(target, dialect=dialect)
        if warehouse_overlay is not None:
            project = _with_warehouse_overlay(project, warehouse_overlay)
        return project
    raise LineageInputError(f"Unsupported project input path: {target}")


def _load_manifest_project(
    path: Path,
    *,
    dialect: str,
    warehouse_overlay: ResolverTypeOverlay | None,
    strict_types: bool,
) -> ProjectInput:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LineageInputError(f"Manifest is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise LineageInputError(f"Manifest root must be an object: {path}")
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, dict):
        raise LineageInputError(f"Manifest is missing a nodes object: {path}")

    node_payloads = _collect_manifest_node_payloads(payload)
    unique_id_to_identity = _build_unique_id_to_identity(node_payloads)
    manifest_types: dict[str, dict[str, str | None]] = {}

    datasets: dict[str, ProjectDataset] = {}
    models_total = 0
    models_with_compiled_sql = 0
    for unique_id, node_payload in node_payloads.items():
        resource_type = str(node_payload.get("resource_type") or "").strip().lower()
        if resource_type not in {"model", "seed", "source"}:
            continue
        identity = unique_id_to_identity[unique_id]
        dbt_fields = _dbt_metadata_fields(node_payload, unique_id=unique_id)
        declared_columns, raw_types = _column_metadata_from_manifest_node(node_payload)
        manifest_types[identity] = raw_types
        if resource_type == "model":
            models_total += 1
            sql = _read_compiled_sql(path, node_payload)
            models_with_compiled_sql += 1
            depends_on = _resolve_manifest_dependencies(
                node_payload,
                unique_id_to_identity=unique_id_to_identity,
            )
            datasets[identity] = ProjectDataset(
                name=identity,
                kind="local",
                sql=sql,
                dependency_names=depends_on,
                declared_columns=declared_columns,
                evidence_file=_compiled_path_label(node_payload),
                **dbt_fields,
            )
        else:
            datasets[identity] = ProjectDataset(
                name=identity,
                kind="root",
                sql=None,
                dependency_names=(),
                declared_columns=declared_columns,
                evidence_file=None,
                **dbt_fields,
            )

    if not datasets:
        raise LineageInputError(f"Manifest produced no usable datasets: {path}")

    datasets = _ensure_root_dependencies(datasets)

    catalog_path = path.parent / "catalog.json"
    catalog_overlay = overlay_types_from_dbt_catalog(
        cast(Mapping[str, _TypedDataset], datasets),
        catalog_path,
        dialect=dialect,
        strict=strict_types,
    )
    type_warnings: list[Warning] = []
    merged_datasets: dict[str, ProjectDataset] = {}
    for name, dataset in datasets.items():
        column_types, type_sources, resolved_warnings = resolve_dataset_column_types(
            dataset_name=name,
            declared_columns=dataset.declared_columns,
            manifest_types=manifest_types.get(name, {}),
            catalog_overlay=catalog_overlay,
            warehouse_overlay=warehouse_overlay,
            dialect=dialect,
            strict=strict_types,
        )
        type_warnings.extend(resolved_warnings)
        merged_datasets[name] = ProjectDataset(
            name=dataset.name,
            kind=dataset.kind,
            sql=dataset.sql,
            dependency_names=dataset.dependency_names,
            declared_columns=dataset.declared_columns,
            column_types=column_types,
            type_sources=type_sources,
            evidence_file=dataset.evidence_file,
            unique_id=dataset.unique_id,
            package_name=dataset.package_name,
            manifest_name=dataset.manifest_name,
            alias=dataset.alias,
            database=dataset.database,
            schema_name=dataset.schema_name,
            relation_name=dataset.relation_name,
            resource_type=dataset.resource_type,
        )

    project_name = _manifest_project_name(payload)
    label = project_name or path.parent.name
    compile_report = ManifestCompileReport(
        models_total=models_total,
        models_with_compiled_sql=models_with_compiled_sql,
        models_missing_compiled_sql=(),
    )
    return ProjectInput(
        input_kind="dbt_manifest",
        label=label,
        datasets=merged_datasets,
        manifest_compile_report=compile_report,
        type_warnings=tuple(type_warnings),
    )


def _collect_manifest_node_payloads(payload: dict) -> dict[str, dict]:
    collected: dict[str, dict] = {}
    for section in ("nodes", "sources"):
        section_payload = payload.get(section)
        if section_payload is None and section == "sources":
            continue
        if not isinstance(section_payload, dict):
            raise LineageInputError(f"Manifest {section!r} section must be an object")
        for unique_id, node_payload in section_payload.items():
            if not isinstance(node_payload, dict):
                raise LineageInputError(
                    f"Manifest {section!r} entry {unique_id!r} must be an object"
                )
            collected[str(unique_id)] = node_payload
    return collected


def _manifest_project_name(payload: dict) -> str:
    metadata = payload.get("metadata", {})
    if metadata is None:
        return ""
    if not isinstance(metadata, dict):
        raise LineageInputError("Manifest metadata must be an object")
    return str(metadata.get("project_name") or "").strip()


def _build_unique_id_to_identity(node_payloads: dict[str, dict]) -> dict[str, str]:
    unique_id_to_identity: dict[str, str] = {}
    identity_to_unique_id: dict[str, str] = {}
    for unique_id, node_payload in node_payloads.items():
        resource_type = str(node_payload.get("resource_type") or "").strip().lower()
        if resource_type not in {"model", "seed", "source"}:
            continue
        identity = resolve_dbt_dataset_identity(node_payload)
        existing = identity_to_unique_id.get(identity)
        if existing is not None and existing != unique_id:
            raise LineageInputError(f"Duplicate dbt dataset identity {identity!r}")
        unique_id_to_identity[unique_id] = identity
        identity_to_unique_id[identity] = unique_id
    return unique_id_to_identity


def resolve_dbt_dataset_identity(node_payload: dict) -> str:
    relation_name = str(node_payload.get("relation_name") or "").strip()
    if relation_name:
        return normalize_identifier(relation_name)
    database = str(node_payload.get("database") or "").strip()
    schema = str(node_payload.get("schema") or "").strip()
    alias = str(node_payload.get("alias") or "").strip()
    name = str(node_payload.get("name") or "").strip()
    if database and schema and alias:
        return normalize_identifier(f"{database}.{schema}.{alias}")
    if schema and alias:
        return normalize_identifier(f"{schema}.{alias}")
    if alias:
        return normalize_identifier(alias)
    if database and schema and name:
        return normalize_identifier(f"{database}.{schema}.{name}")
    if schema and name:
        return normalize_identifier(f"{schema}.{name}")
    if name:
        return normalize_identifier(name)
    raise LineageInputError("dbt manifest node is missing a usable identity")


def resolve_ref_relation(
    reference: str,
    *,
    project: ProjectInput,
    alias_map: dict[str, str] | None = None,
) -> str:
    """Resolve a SQL ref/source reference to canonical relation id at the load boundary."""
    from .relations import normalize_relation_id

    return normalize_relation_id(reference, project=project, alias_map=alias_map)


def dbt_aspect_for_dataset(dataset: ProjectDataset) -> dict[str, str] | None:
    if dataset.unique_id is None:
        return None
    aspect = {
        "unique_id": dataset.unique_id,
        "package_name": dataset.package_name or "",
        "name": dataset.manifest_name or leaf_name(dataset.name),
        "alias": dataset.alias or "",
        "database": dataset.database or "",
        "schema": dataset.schema_name or "",
        "relation_name": dataset.relation_name or "",
        "resource_type": dataset.resource_type or "",
    }
    return {key: value for key, value in aspect.items() if value}


def _dbt_metadata_fields(node_payload: dict, *, unique_id: str) -> DbtDatasetMetadata:
    return {
        "unique_id": unique_id,
        "package_name": _optional_string(node_payload.get("package_name")),
        "manifest_name": _optional_string(node_payload.get("name")),
        "alias": _optional_string(node_payload.get("alias")),
        "database": _optional_string(node_payload.get("database")),
        "schema_name": _optional_string(node_payload.get("schema")),
        "relation_name": _optional_string(node_payload.get("relation_name")),
        "resource_type": _optional_string(node_payload.get("resource_type")),
    }


def _optional_string(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _resolve_manifest_dependencies(
    node_payload: dict,
    *,
    unique_id_to_identity: dict[str, str],
) -> tuple[str, ...]:
    depends_on: list[str] = []
    for dependency_unique_id in _manifest_dependency_ids(node_payload):
        resolved = unique_id_to_identity.get(dependency_unique_id)
        if resolved is None:
            if dependency_unique_id.startswith("source."):
                resolved = normalize_identifier(dependency_unique_id.split(".")[-1])
            else:
                raise LineageInputError(
                    f"Unresolved dbt dependency {dependency_unique_id!r}"
                )
        depends_on.append(resolved)
    return tuple(depends_on)


def _manifest_dependency_ids(node_payload: dict) -> tuple[str, ...]:
    depends_on = node_payload.get("depends_on", {})
    if depends_on is None:
        return ()
    if not isinstance(depends_on, dict):
        raise LineageInputError("Manifest depends_on must be an object")
    nodes = depends_on.get("nodes", [])
    if nodes is None:
        return ()
    if not isinstance(nodes, list):
        raise LineageInputError("Manifest depends_on.nodes must be a list")

    dependency_ids: list[str] = []
    for dependency in nodes:
        if not isinstance(dependency, str):
            raise LineageInputError("Manifest dependency ids must be strings")
        dependency_unique_id = dependency.strip()
        if dependency_unique_id:
            dependency_ids.append(dependency_unique_id)
    return tuple(dependency_ids)


def _ensure_root_dependencies(
    datasets: dict[str, ProjectDataset],
) -> dict[str, ProjectDataset]:
    updated = dict(datasets)
    for dataset in list(updated.values()):
        if dataset.kind != "local":
            continue
        for dependency_name in dataset.dependency_names:
            if dependency_name in updated:
                continue
            updated[dependency_name] = ProjectDataset(
                name=dependency_name,
                kind="root",
                sql=None,
                dependency_names=(),
                declared_columns=(),
                evidence_file=None,
            )
    return updated


def _read_compiled_sql(manifest_path: Path, node_payload: dict) -> str:
    compiled_code = str(node_payload.get("compiled_code") or "").strip()
    if compiled_code:
        return compiled_code
    compiled_sql = str(node_payload.get("compiled_sql") or "").strip()
    if compiled_sql:
        return compiled_sql

    compiled_path = str(node_payload.get("compiled_path") or "").strip()
    if compiled_path:
        candidate = _resolve_manifest_relative_path(manifest_path, compiled_path)
        sql = candidate.read_text(encoding="utf-8").strip()
        if sql:
            return sql

    raise LineageInputError(
        f"Manifest model {node_payload.get('name')!r} is missing compiled SQL."
    )


def _compiled_path_label(node_payload: dict) -> str | None:
    compiled_path = str(node_payload.get("compiled_path") or "").strip()
    if compiled_path:
        return compiled_path
    name = str(node_payload.get("name") or "").strip()
    return f"{name}.sql" if name else None


def _resolve_manifest_relative_path(manifest_path: Path, relative_path: str) -> Path:
    manifest_root = manifest_path.parent.resolve()
    candidate = (manifest_root / relative_path).resolve()
    if not candidate.is_relative_to(manifest_root):
        raise LineageInputError(
            f"Manifest compiled_path escapes the manifest directory: {relative_path!r}"
        )
    if not candidate.is_file():
        raise LineageInputError(
            f"Manifest compiled_path is not a readable file: {relative_path!r}"
        )
    return candidate


def _column_metadata_from_manifest_node(
    node_payload: dict,
) -> tuple[tuple[str, ...], dict[str, str | None]]:
    columns = node_payload.get("columns", {})
    if columns is None:
        return (), {}
    if not isinstance(columns, dict):
        raise LineageInputError("Manifest columns must be an object")

    column_names: list[str] = []
    raw_types: dict[str, str | None] = {}
    for column_name, column_payload in columns.items():
        if not isinstance(column_payload, dict):
            raise LineageInputError(
                f"Manifest column entry {column_name!r} must be an object"
            )
        name = str(column_payload.get("name") or column_name).strip()
        if name:
            column_names.append(name)
            data_type = column_payload.get("data_type")
            raw_types[name] = str(data_type).strip() if data_type else None
    return tuple(column_names), raw_types


def _load_sql_folder_project(path: Path, *, dialect: str) -> ProjectInput:
    sql_files = sorted(path.rglob("*.sql"))
    if not sql_files:
        raise LineageInputError(f"SQL folder contains no .sql files: {path}")

    datasets: dict[str, ProjectDataset] = {}
    raw_sql_by_name: dict[str, str] = {}
    for sql_file in sql_files:
        relative_parts = sql_file.relative_to(path).with_suffix("").parts
        dataset_name = normalize_identifier(".".join(relative_parts))
        if dataset_name in datasets:
            raise LineageInputError(
                f"SQL folder produced duplicate dataset name {dataset_name!r}."
            )
        sql = sql_file.read_text(encoding="utf-8").strip()
        if not sql:
            raise LineageInputError(f"SQL file is empty: {sql_file}")
        raw_sql_by_name[dataset_name] = sql
        datasets[dataset_name] = ProjectDataset(
            name=dataset_name,
            kind="local",
            sql=sql,
            dependency_names=(),
            declared_columns=(),
            evidence_file=str(sql_file.relative_to(path)),
        )

    local_names = set(raw_sql_by_name)
    for dataset_name, sql in raw_sql_by_name.items():
        try:
            dependency_names = sorted(
                {
                    normalize_identifier(reference)
                    for reference in list_table_references(sql, dialect=dialect)
                    if normalize_identifier(reference) in local_names
                }
            )
        except LineageInputError:
            dependency_names = ()
        current = datasets[dataset_name]
        datasets[dataset_name] = ProjectDataset(
            name=current.name,
            kind=current.kind,
            sql=current.sql,
            dependency_names=tuple(dependency_names),
            declared_columns=current.declared_columns,
            evidence_file=current.evidence_file,
        )

    return ProjectInput(
        input_kind="sql_folder",
        label=path.name,
        datasets=datasets,
    )


def _with_warehouse_overlay(
    project: ProjectInput,
    overlay: ResolverTypeOverlay,
) -> ProjectInput:
    return ProjectInput(
        input_kind=project.input_kind,
        label=project.label,
        datasets=_apply_warehouse_overlay_to_datasets(project.datasets, overlay),
        manifest_compile_report=project.manifest_compile_report,
        type_warnings=project.type_warnings,
    )


def _apply_warehouse_overlay_to_datasets(
    datasets: dict[str, ProjectDataset],
    overlay: ResolverTypeOverlay,
) -> dict[str, ProjectDataset]:
    updated = dict(datasets)
    for dataset_name, column_entries in overlay.by_dataset.items():
        column_types = {
            column_name: entry.sqlglot_type
            for column_name, entry in column_entries.items()
        }
        type_sources: dict[str, TypeSource] = {
            column_name: entry.source for column_name, entry in column_entries.items()
        }
        declared_columns = tuple(sorted(column_entries))
        if dataset_name not in updated:
            updated[dataset_name] = ProjectDataset(
                name=dataset_name,
                kind="root",
                sql=None,
                dependency_names=(),
                declared_columns=declared_columns,
                evidence_file=None,
                column_types=column_types,
                type_sources=type_sources,
            )
            continue
        existing = updated[dataset_name]
        merged_types = dict(existing.column_types)
        merged_types.update(column_types)
        merged_sources = dict(existing.type_sources)
        merged_sources.update(type_sources)
        merged_declared = tuple(
            dict.fromkeys((*existing.declared_columns, *declared_columns))
        )
        updated[dataset_name] = ProjectDataset(
            name=existing.name,
            kind=existing.kind,
            sql=existing.sql,
            dependency_names=existing.dependency_names,
            declared_columns=merged_declared,
            evidence_file=existing.evidence_file,
            column_types=merged_types,
            type_sources=merged_sources,
            unique_id=existing.unique_id,
            package_name=existing.package_name,
            manifest_name=existing.manifest_name,
            alias=existing.alias,
            database=existing.database,
            schema_name=existing.schema_name,
            relation_name=existing.relation_name,
            resource_type=existing.resource_type,
        )
    return updated


def transitive_local_dependencies(
    project: ProjectInput,
    *,
    seeds: frozenset[str],
) -> frozenset[str]:
    """Return all local datasets reachable upstream from ``seeds``."""
    local_names = project.local_dataset_names()
    missing = sorted(seed for seed in seeds if seed not in project.datasets)
    if missing:
        raise LineageInputError(
            f"transitive_local_dependencies missing seed datasets: {missing}"
        )
    visited: set[str] = set()
    stack = list(seeds)
    while stack:
        dataset_name = stack.pop()
        if dataset_name not in local_names or dataset_name in visited:
            continue
        visited.add(dataset_name)
        stack.extend(project.datasets[dataset_name].dependency_names)
    return frozenset(visited)


def subset_project(
    project: ProjectInput,
    model_names: frozenset[str],
) -> ProjectInput:
    """Return a project containing only ``model_names`` with closed local deps."""
    missing = sorted(name for name in model_names if name not in project.datasets)
    if missing:
        raise LineageInputError(f"subset_project missing datasets: {missing}")
    local_names = project.local_dataset_names()
    for name in model_names:
        dataset = project.datasets[name]
        for dependency_name in dataset.dependency_names:
            if (
                dependency_name in local_names
                and dependency_name not in model_names
            ):
                raise LineageInputError(
                    "subset_project closure incomplete: "
                    f"{name} depends on local {dependency_name!r} not in model_names"
                )
    datasets = {
        name: project.datasets[name] for name in sorted(model_names)
    }
    return ProjectInput(
        input_kind=project.input_kind,
        label=project.label,
        datasets=datasets,
        manifest_compile_report=project.manifest_compile_report,
        type_warnings=project.type_warnings,
    )


def _manifest_identity_index(nodes: dict[str, dict]) -> dict[str, str]:
    index: dict[str, str] = {}
    for uid, node in nodes.items():
        if not uid.startswith("model."):
            continue
        try:
            identity = resolve_dbt_dataset_identity(node)
        except LineageInputError:
            continue
        canonical_keys = {normalize_identifier(identity)}
        canonical_keys.update(relation_fqn_lookup_keys(identity))
        short_keys: set[str] = set()
        alias = str(node.get("alias") or "").strip()
        name = str(node.get("name") or "").strip()
        if alias:
            short_keys.add(normalize_identifier(alias))
        if name:
            short_keys.add(normalize_identifier(name))
        short_keys.difference_update(canonical_keys)

        for key in canonical_keys:
            if not key:
                continue
            existing = index.get(key)
            if existing is not None and existing != uid:
                raise LineageInputError(
                    "Ambiguous manifest identity index: "
                    f"key {key!r} maps to both {existing!r} and {uid!r}."
                )
            index[key] = uid

        for key in short_keys:
            if not key:
                continue
            existing = index.get(key)
            if existing is not None and existing != uid:
                continue
            index[key] = uid
    return index


def _resolve_manifest_model_uids(
    nodes: dict[str, dict],
    model_identities: set[str],
) -> dict[str, str]:
    identity_index = _manifest_identity_index(nodes)
    resolved: dict[str, str] = {}
    for model_identity in model_identities:
        keys = {normalize_identifier(model_identity)}
        keys.update(relation_fqn_lookup_keys(model_identity))
        matched: set[str] = set()
        for key in keys:
            uid = identity_index.get(key)
            if uid is not None:
                matched.add(uid)
        if len(matched) > 1:
            raise LineageInputError(
                f"Ambiguous manifest identity for {model_identity!r}: "
                f"matched uids {sorted(matched)}"
            )
        if len(matched) == 0:
            raise LineageInputError(
                f"Model identity {model_identity!r} not found in manifest nodes."
            )
        resolved[model_identity] = next(iter(matched))
    return resolved


def _apply_transitive_dbt_deps(nodes: dict[str, dict], included: set[str]) -> None:
    pending = set(included)
    while pending:
        uid = pending.pop()
        node = nodes.get(uid, {})
        depends = node.get("depends_on", {})
        if not isinstance(depends, dict):
            continue
        for dep in depends.get("nodes", []):
            dep_uid = str(dep)
            if not dep_uid.startswith("model."):
                continue
            if dep_uid in included or dep_uid not in nodes:
                continue
            included.add(dep_uid)
            pending.add(dep_uid)


def _apply_sql_visible_models_single_hop(
    nodes: dict[str, dict],
    included: set[str],
    *,
    dialect: str,
    identity_index: dict[str, str],
) -> None:
    for uid in tuple(included):
        node = nodes.get(uid, {})
        compiled = str(node.get("compiled_code") or "").strip()
        if not compiled:
            continue
        for reference in list_table_references(compiled, dialect=dialect):
            keys = {normalize_identifier(reference)}
            keys.update(relation_fqn_lookup_keys(reference))
            matched_uids: set[str] = set()
            for key in keys:
                matched_uid = identity_index.get(key)
                if matched_uid is not None:
                    matched_uids.add(matched_uid)
            if len(matched_uids) > 1:
                raise LineageInputError(
                    f"Ambiguous SQL reference {reference!r} in {uid!r} "
                    f"matched model uids: {sorted(matched_uids)}"
                )
            if len(matched_uids) == 1:
                included.add(next(iter(matched_uids)))


def manifest_model_closure(
    manifest_path: Path,
    seed_uids: frozenset[str],
    *,
    dialect: str = "duckdb",
    with_dbt_deps: bool = True,
    with_sql_visible_hop: bool = True,
) -> frozenset[str]:
    """Return manifest model uids reachable from seeds via closure rules."""
    if not seed_uids:
        raise LineageInputError("manifest_model_closure requires at least one seed uid.")

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    nodes = payload.get("nodes", {})
    if not isinstance(nodes, dict):
        raise LineageInputError(f"Manifest missing nodes object: {manifest_path}")

    missing_seeds = sorted(uid for uid in seed_uids if uid not in nodes)
    if missing_seeds:
        raise LineageInputError(
            f"Seed model uids missing from manifest {manifest_path}: {missing_seeds}"
        )

    included = set(seed_uids)
    if with_dbt_deps:
        _apply_transitive_dbt_deps(nodes, included)
    if with_sql_visible_hop:
        identity_index = _manifest_identity_index(nodes)
        _apply_sql_visible_models_single_hop(
            nodes,
            included,
            dialect=dialect,
            identity_index=identity_index,
        )
    if not included:
        raise LineageInputError(
            f"Manifest closure included zero models from {manifest_path}."
        )
    return frozenset(included)


def manifest_model_closure_for_identities(
    full_manifest_path: Path,
    model_identities: set[str],
    *,
    dialect: str = "duckdb",
    with_dbt_deps: bool = True,
    with_sql_visible_hop: bool = True,
) -> frozenset[str]:
    """Resolve qualified identities to uids and return their union closure."""
    payload = json.loads(full_manifest_path.read_text(encoding="utf-8"))
    nodes = payload.get("nodes", {})
    if not isinstance(nodes, dict):
        raise LineageInputError(
            f"Manifest missing nodes object: {full_manifest_path}"
        )
    if not model_identities:
        raise LineageInputError(
            "manifest_model_closure_for_identities requires model names."
        )

    uid_by_identity = _resolve_manifest_model_uids(nodes, model_identities)
    included: set[str] = set()
    for uid in uid_by_identity.values():
        closure = manifest_model_closure(
            full_manifest_path,
            frozenset({uid}),
            dialect=dialect,
            with_dbt_deps=with_dbt_deps,
            with_sql_visible_hop=with_sql_visible_hop,
        )
        included.update(closure)
    return frozenset(included)


def slice_intersect_build_scope(
    *,
    full_manifest_path: Path,
    slice_manifest_path: Path,
    target_models: Sequence[str],
    dialect: str,
) -> frozenset[str]:
    """Local model names required to build lineage for targets within a manifest slice."""
    if not target_models:
        raise LineageInputError("slice_intersect_build_scope requires target_models.")

    slice_project = load_project(slice_manifest_path, dialect=dialect)
    slice_names = set(slice_project.datasets.keys())
    missing_targets = sorted(
        model_name for model_name in target_models if model_name not in slice_names
    )
    if missing_targets:
        raise LineageInputError(
            "Target models missing from slice manifest "
            f"{slice_manifest_path}: {missing_targets}"
        )

    closure_uids = manifest_model_closure_for_identities(
        full_manifest_path,
        set(target_models),
        dialect=dialect,
        with_dbt_deps=True,
        with_sql_visible_hop=True,
    )

    payload = json.loads(full_manifest_path.read_text(encoding="utf-8"))
    nodes = payload.get("nodes", {})
    if not isinstance(nodes, dict):
        raise LineageInputError(
            f"Full manifest missing nodes object: {full_manifest_path}"
        )

    closure_identities: set[str] = set()
    for uid in closure_uids:
        node = nodes.get(uid)
        if not isinstance(node, dict):
            raise LineageInputError(
                f"Closure uid {uid!r} missing from full manifest {full_manifest_path}."
            )
        closure_identities.add(resolve_dbt_dataset_identity(node))

    build_scope = frozenset(closure_identities & slice_names)
    missing_build = sorted(
        model_name for model_name in target_models if model_name not in build_scope
    )
    if missing_build:
        raise LineageInputError(
            "Manifest closure did not include all target models in slice build scope: "
            f"{missing_build}"
        )
    if not build_scope:
        raise LineageInputError(
            "Slice build scope is empty after manifest intersection."
        )
    return build_scope


def union_build_scope_for_targets(
    project: ProjectInput,
    *,
    full_manifest_path: Path,
    slice_manifest_path: Path,
    target_models: Sequence[str],
    dialect: str,
) -> frozenset[str]:
    """Dependency-closed union of per-target slice build scopes."""
    seeds: set[str] = set()
    for target in target_models:
        seeds.update(
            slice_intersect_build_scope(
                full_manifest_path=full_manifest_path,
                slice_manifest_path=slice_manifest_path,
                target_models=[target],
                dialect=dialect,
            )
        )
    return transitive_local_dependencies(project, seeds=frozenset(seeds))
