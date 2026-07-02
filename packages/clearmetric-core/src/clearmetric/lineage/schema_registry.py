"""Schema registry — single source of truth for relation column knowledge."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

from clearmetric.core import normalize_identifier

from .errors import LineageInputError
from .loaders import ProjectDataset, ProjectInput
from .relations import (
    normalize_relation_id,
    relation_fqn_lookup_keys,
    relation_lookup_keys,
    resolve_sql_visible_table_ref,
)
from .resolver_status import TypeStatus, derive_type_status

SchemaSource = Literal["manifest", "catalog", "warehouse", "inferred", "declared"]


@dataclass(frozen=True)
class RelationSchema:
    relation_id: str
    column_names: tuple[str, ...]
    column_types: dict[str, str]
    type_status: TypeStatus
    schema_source: SchemaSource
    names_only: bool = False

    def has_column(self, column_name: str) -> bool:
        return column_name in self.column_names

    def sqlglot_columns(self) -> dict[str, str]:
        return dict(self.column_types)


@dataclass(frozen=True)
class MissingSchema:
    relation_id: str
    reason: str = "columns_unavailable"


@dataclass(frozen=True)
class UnknownRelation:
    reference: str
    reason: str = "identity_unresolved"


@dataclass(frozen=True)
class LineageSchemaSnapshot:
    """Schema visible during Pass B plus unresolved SQL-visible relations."""

    schema: dict[str, dict[str, str]]
    unresolved: tuple[MissingSchema | UnknownRelation, ...] = ()


@dataclass
class SchemaRegistry:
    """Canonical relation schemas keyed by normalize_relation_id output."""

    _by_id: dict[str, RelationSchema] = field(default_factory=dict)
    _alias_index: dict[str, str] = field(default_factory=dict)
    _ambiguous_aliases: dict[str, frozenset[str]] = field(default_factory=dict)
    _project: ProjectInput | None = None

    @classmethod
    def from_project(cls, project: ProjectInput, *, dialect: str) -> SchemaRegistry:
        del dialect
        registry = cls(_project=project)
        for dataset in project.datasets.values():
            registry.seed_dataset(dataset)
        return registry

    def seed_dataset(self, dataset: ProjectDataset) -> None:
        canonical = normalize_relation_id(dataset.name, project=self._project)
        column_names = tuple(sorted(set(dataset.declared_columns)))
        column_types = dict(dataset.column_types)
        type_status = derive_type_status(
            column_names=column_names,
            column_types=column_types,
        )
        schema = RelationSchema(
            relation_id=canonical,
            column_names=column_names,
            column_types=column_types,
            type_status=type_status,
            schema_source="manifest" if column_types else "declared",
            names_only=type_status == "names_only",
        )
        self._register_schema(schema)
        for key in relation_lookup_keys(dataset):
            self._register_alias_key(key, canonical)

    def resolve_relation(
        self,
        reference: str,
        *,
        alias_map: dict[str, str] | None = None,
    ) -> RelationSchema | MissingSchema | UnknownRelation:
        try:
            canonical = normalize_relation_id(
                reference,
                project=self._project,
                alias_map=alias_map,
            )
        except LineageInputError:
            return UnknownRelation(reference=reference)

        schema = self._by_id.get(canonical)
        if schema is None:
            if self._project is not None and canonical in self._project.datasets:
                dataset = self._project.datasets[canonical]
                if not dataset.declared_columns:
                    return MissingSchema(relation_id=canonical)
                return MissingSchema(relation_id=canonical, reason="not_registered")
            return UnknownRelation(reference=reference)

        if not schema.column_names:
            return MissingSchema(relation_id=canonical)
        return schema

    def get_columns(
        self,
        reference: str,
        *,
        alias_map: dict[str, str] | None = None,
    ) -> tuple[str, ...]:
        resolved = self.resolve_relation(reference, alias_map=alias_map)
        if isinstance(resolved, RelationSchema):
            return resolved.column_names
        return ()

    def has_column(
        self,
        reference: str,
        column_name: str,
        *,
        alias_map: dict[str, str] | None = None,
    ) -> bool:
        resolved = self.resolve_relation(reference, alias_map=alias_map)
        return isinstance(resolved, RelationSchema) and resolved.has_column(column_name)

    def source_of(self, reference: str) -> SchemaSource | None:
        resolved = self.resolve_relation(reference)
        if isinstance(resolved, RelationSchema):
            return resolved.schema_source
        return None

    def register_inferred_outputs(
        self,
        relation_id: str,
        *,
        column_names: tuple[str, ...],
        column_types: dict[str, str] | None = None,
        schema_source: SchemaSource = "inferred",
    ) -> None:
        canonical = normalize_relation_id(relation_id, project=self._project)
        types = dict(column_types or {})
        merged_names = tuple(sorted(set(column_names)))
        existing = self._by_id.get(canonical)
        if existing is not None:
            merged_names = tuple(sorted(set(existing.column_names) | set(merged_names)))
            types = {**existing.column_types, **types}
        type_status = derive_type_status(
            column_names=merged_names,
            column_types=types,
        )
        schema = RelationSchema(
            relation_id=canonical,
            column_names=merged_names,
            column_types=types,
            type_status=type_status,
            schema_source=schema_source,
            names_only=type_status == "names_only",
        )
        self._register_schema(schema)

    def to_lineage_schema(self) -> dict[str, dict[str, str]]:
        """Unified schema for lineage() and star_expansion_policy."""
        result: dict[str, dict[str, str]] = {}
        for relation_id, schema in self._by_id.items():
            row: dict[str, str] = {}
            for name in schema.column_names:
                row[name] = schema.column_types.get(name, "")
            if row:
                result[relation_id] = row
        for key, target in self._alias_index.items():
            if target in result:
                result[key] = dict(result[target])
        return result

    def _transitive_dependency_names(
        self,
        dataset: ProjectDataset,
        project: ProjectInput,
    ) -> set[str]:
        upstream: set[str] = set()
        stack = list(dataset.dependency_names)
        while stack:
            dependency_name = stack.pop()
            if dependency_name in upstream:
                continue
            dep_dataset = project.datasets.get(dependency_name)
            if dep_dataset is None:
                continue
            upstream.add(dependency_name)
            stack.extend(dep_dataset.dependency_names)
        visible_canonical: set[str] = set()
        for name in upstream:
            visible_canonical.add(name)
            try:
                visible_canonical.add(
                    normalize_relation_id(name, project=self._project)
                )
            except LineageInputError:
                continue
        visible = set(visible_canonical)
        for alias_key, target in self._alias_index.items():
            if target in visible_canonical:
                visible.add(alias_key)
        return visible

    def lineage_schema_for_model(
        self,
        dataset: ProjectDataset,
        project: ProjectInput,
        *,
        table_references: Sequence[str] = (),
        alias_map: dict[str, str] | None = None,
        cte_names: set[str] | frozenset[str] = frozenset(),
    ) -> LineageSchemaSnapshot:
        """Schema visible during Pass B: transitive deps plus SQL-visible relations."""
        visible = self._transitive_dependency_names(dataset, project)
        unresolved: list[MissingSchema | UnknownRelation] = []
        seen_unresolved: set[str] = set()

        for reference in table_references:
            try:
                qualified = resolve_sql_visible_table_ref(
                    reference,
                    alias_map=alias_map,
                    table_references=table_references,
                    cte_names=cte_names,
                    project=project,
                )
            except LineageInputError:
                key = normalize_identifier(reference)
                if key not in seen_unresolved:
                    seen_unresolved.add(key)
                    unresolved.append(UnknownRelation(reference=reference))
                continue
            if qualified in cte_names:
                continue
            resolved = self.resolve_relation(qualified, alias_map=alias_map)
            if isinstance(resolved, RelationSchema):
                visible.add(resolved.relation_id)
                for key in relation_fqn_lookup_keys(resolved.relation_id):
                    visible.add(key)
                for alias_key, target in self._alias_index.items():
                    if target == resolved.relation_id:
                        visible.add(alias_key)
                continue
            if not self._should_report_unresolved_sql_ref(reference, resolved):
                continue
            if isinstance(resolved, MissingSchema):
                unresolved_key = resolved.relation_id
            else:
                unresolved_key = resolved.reference
            if unresolved_key not in seen_unresolved:
                seen_unresolved.add(unresolved_key)
                unresolved.append(resolved)

        excluded = {
            dataset.name,
            normalize_relation_id(dataset.name, project=self._project),
        }
        for key, target in self._alias_index.items():
            if target in excluded:
                excluded.add(key)
        full = self.to_lineage_schema()
        schema = {
            table: dict(columns)
            for table, columns in full.items()
            if table in visible and table not in excluded
        }
        return LineageSchemaSnapshot(schema=schema, unresolved=tuple(unresolved))

    def to_lineage_schema_for_model(
        self,
        dataset: ProjectDataset,
        project: ProjectInput,
        *,
        table_references: Sequence[str] = (),
        alias_map: dict[str, str] | None = None,
        cte_names: set[str] | frozenset[str] = frozenset(),
    ) -> dict[str, dict[str, str]]:
        """Backward-compatible wrapper returning schema dict only."""
        return self.lineage_schema_for_model(
            dataset,
            project,
            table_references=table_references,
            alias_map=alias_map,
            cte_names=cte_names,
        ).schema

    def _should_report_unresolved_sql_ref(
        self,
        reference: str,
        resolved: MissingSchema | UnknownRelation,
    ) -> bool:
        """Only canonical or known project refs are loud schema blockers."""
        if isinstance(resolved, MissingSchema):
            return True
        normalized = normalize_identifier(reference)
        return normalized in self._alias_index

    def register_lineage_outputs(
        self,
        relation_id: str,
        *,
        output_map_keys: set[str],
        declared_column_types: dict[str, str] | None = None,
    ) -> None:
        """Register inferred output columns after Pass B lineage for one model."""
        canonical = normalize_relation_id(relation_id, project=self._project)
        propagated: dict[str, str] = {}
        existing = self._by_id.get(canonical)
        if existing is not None:
            for name in existing.column_names:
                if name in existing.column_types:
                    propagated[name] = existing.column_types[name]
        if declared_column_types:
            for name, typ in declared_column_types.items():
                if typ:
                    propagated[name] = typ
        all_names = tuple(sorted(set(output_map_keys) | set(propagated.keys())))
        self.register_inferred_outputs(
            canonical,
            column_names=all_names,
            column_types={name: typ for name, typ in propagated.items() if typ},
            schema_source="inferred",
        )

    def to_snapshot(self) -> dict[str, dict[str, str]]:
        """Full names with types where known — for reports and DataHub resolver."""
        snapshot: dict[str, dict[str, str]] = {}
        for relation_id, schema in self._by_id.items():
            row: dict[str, str] = {}
            for name in schema.column_names:
                if name in schema.column_types:
                    row[name] = schema.column_types[name]
                else:
                    row[name] = ""
            if row:
                snapshot[relation_id] = row
        return snapshot

    def snapshot_metadata(self) -> dict[str, dict[str, object]]:
        """Per-relation schema metadata for trace and compare diagnostics."""
        metadata: dict[str, dict[str, object]] = {}
        for relation_id, schema in self._by_id.items():
            metadata[relation_id] = {
                "names_only": schema.names_only,
                "schema_source": schema.schema_source,
                "column_count": len(schema.column_names),
                "typed_column_count": len(schema.column_types),
            }
        return metadata

    def _register_schema(self, schema: RelationSchema) -> None:
        self._by_id[schema.relation_id] = schema

    def _register_alias_key(self, key: str, canonical_id: str) -> None:
        existing = self._alias_index.get(key)
        if existing is not None and existing != canonical_id:
            self._alias_index.pop(key, None)
            self._ambiguous_aliases[key] = frozenset({existing, canonical_id})
            return
        if key in self._ambiguous_aliases:
            self._ambiguous_aliases[key] = frozenset(
                {*self._ambiguous_aliases[key], canonical_id}
            )
            return
        self._alias_index[key] = canonical_id
