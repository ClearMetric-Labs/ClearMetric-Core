"""Canonical relation ID normalization for resolver internals."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from clearmetric.core import normalize_identifier, normalize_identifier_part

from .errors import LineageInputError

if TYPE_CHECKING:
    from .loaders import ProjectDataset, ProjectInput

_RELATION_SEGMENT_COUNT = 3


@dataclass(frozen=True)
class RelationIdParts:
    database: str
    schema: str
    model: str

    @property
    def canonical(self) -> str:
        return normalize_identifier(f"{self.database}.{self.schema}.{self.model}")


def parse_canonical_relation_id(relation_id: str) -> RelationIdParts:
    """Parse a canonical `<database>.<schema>.<model>` relation id."""
    cleaned = normalize_identifier(str(relation_id or "").strip())
    if not cleaned:
        raise LineageInputError("Relation id is empty.")
    segments = cleaned.split(".")
    if len(segments) < _RELATION_SEGMENT_COUNT:
        raise LineageInputError(
            f"Relation id {relation_id!r} is not canonical "
            f"(expected <database>.<schema>.<model>)."
        )
    if len(segments) > _RELATION_SEGMENT_COUNT:
        database = ".".join(segments[: len(segments) - 2])
        schema = segments[-2]
        model = segments[-1]
    else:
        database, schema, model = segments
    return RelationIdParts(database=database, schema=schema, model=model)


def is_canonical_relation_id(relation_id: str) -> bool:
    try:
        parse_canonical_relation_id(relation_id)
    except LineageInputError:
        return False
    return True


def normalize_datahub_urn(urn: str) -> str | None:
    """Extract dataset name from a DataHub dataset URN when possible."""
    text = str(urn or "").strip()
    if not text.startswith("urn:li:dataset:"):
        return None
    try:
        from datahub.metadata.urns import DatasetUrn

        return normalize_identifier(DatasetUrn.from_string(text).name)
    except (ImportError, AttributeError, ValueError):
        return None


def normalize_relation_id(
    reference: str,
    *,
    project: ProjectInput | None = None,
    alias_map: dict[str, str] | None = None,
) -> str:
    """Normalize any relation reference to canonical `<database>.<schema>.<model>`."""
    raw = str(reference or "").strip()
    if not raw:
        raise LineageInputError("Relation reference is empty.")

    urn_name = normalize_datahub_urn(raw)
    if urn_name is not None:
        raw = urn_name

    if alias_map:
        aliased = alias_map.get(normalize_identifier(raw))
        if aliased:
            raw = aliased

    candidate = normalize_identifier(raw)
    if is_canonical_relation_id(candidate):
        return candidate

    if project is None:
        raise LineageInputError(
            f"Relation reference {reference!r} is not canonical and no project "
            "context was provided for resolution."
        )

    resolved = _resolve_short_reference(candidate, project=project)
    if resolved is None:
        raise LineageInputError(
            f"Unable to resolve relation reference {reference!r} to a canonical id."
        )
    return resolved


def _resolve_short_reference(reference: str, *, project: ProjectInput) -> str | None:
    normalized = normalize_identifier(reference)
    leaf = normalized.split(".")[-1]

    exact_matches: list[str] = []
    leaf_matches: list[str] = []
    alias_matches: list[str] = []

    for dataset in project.datasets.values():
        name = normalize_identifier(dataset.name)
        if name == normalized:
            exact_matches.append(name)
            continue
        if is_canonical_relation_id(name) and name.split(".")[-1] == leaf:
            leaf_matches.append(name)
        manifest_name = str(dataset.manifest_name or "").strip()
        alias = str(dataset.alias or "").strip()
        if manifest_name and normalize_identifier(manifest_name) == normalized:
            alias_matches.append(name)
        if alias and normalize_identifier(alias) == normalized:
            alias_matches.append(name)
        if manifest_name and normalize_identifier(manifest_name) == leaf:
            leaf_matches.append(name)
        if alias and normalize_identifier(alias) == leaf:
            leaf_matches.append(name)

    for bucket in (exact_matches, alias_matches, leaf_matches):
        unique = sorted(set(bucket))
        if len(unique) == 1:
            return unique[0]
        if len(unique) > 1:
            raise LineageInputError(
                f"Ambiguous relation reference {reference!r}; matched {unique!r}."
            )
    return None


def relation_fqn_lookup_keys(relation_id: str) -> frozenset[str]:
    """Qualified alias keys for schema registration and key-gap audit."""
    normalized = normalize_identifier(str(relation_id or "").strip())
    if not normalized:
        return frozenset()
    keys: set[str] = {normalized}
    if is_canonical_relation_id(normalized):
        parts = parse_canonical_relation_id(normalized)
        keys.add(normalize_identifier(f"{parts.schema}.{parts.model}"))
        keys.add(normalize_identifier(f"{parts.database}.{parts.schema}.{parts.model}"))
    else:
        segments = normalized.split(".")
        if len(segments) >= 2:
            keys.add(normalize_identifier(".".join(segments[-2:])))
        if len(segments) >= 3:
            keys.add(normalize_identifier(".".join(segments[-3:])))
    return frozenset(keys)


def _table_reference_lookup_index(
    table_references: Collection[str],
) -> dict[str, str]:
    """Map sql-visible table keys to one qualified reference from compiled SQL."""
    index: dict[str, str] = {}
    ambiguous: set[str] = set()
    for reference in table_references:
        normalized = normalize_identifier(str(reference or "").strip())
        if not normalized:
            continue
        for key in relation_fqn_lookup_keys(normalized):
            existing = index.get(key)
            if existing is not None and existing != normalized:
                ambiguous.add(key)
                continue
            index[key] = normalized
    for key in ambiguous:
        index.pop(key, None)
    return index


def resolve_sql_visible_table_ref(
    reference: str,
    *,
    alias_map: dict[str, str] | None = None,
    table_references: Collection[str] = (),
    cte_names: set[str] | frozenset[str] = frozenset(),
    project: ProjectInput | None = None,
) -> str:
    """Resolve a sqlglot parent table name to the qualified ref visible in SQL."""
    parent_key = normalize_identifier_part(str(reference or "").strip())
    if not parent_key:
        raise LineageInputError("Relation reference is empty.")
    if parent_key in cte_names:
        return parent_key

    candidate = parent_key
    if alias_map:
        aliased = alias_map.get(parent_key)
        if aliased:
            candidate = normalize_identifier(aliased)
    if candidate in cte_names:
        return candidate

    if is_canonical_relation_id(candidate):
        return candidate

    project_resolution_error: LineageInputError | None = None
    if project is not None:
        try:
            return normalize_relation_id(
                candidate,
                project=project,
                alias_map=alias_map,
            )
        except LineageInputError as exc:
            project_resolution_error = exc

    lookup = _table_reference_lookup_index(table_references)
    resolved = lookup.get(normalize_identifier(candidate))
    if resolved is not None:
        return resolved
    resolved = lookup.get(parent_key)
    if resolved is not None:
        return resolved
    message = f"Unable to resolve SQL-visible relation reference {reference!r}."
    if project_resolution_error is not None:
        message = f"{message} Project lookup failed: {project_resolution_error}"
    raise LineageInputError(message)


def relation_lookup_keys(dataset: ProjectDataset) -> frozenset[str]:
    """All alias keys that must normalize to one canonical relation id."""
    keys = {normalize_identifier(dataset.name)}
    if dataset.manifest_name:
        keys.add(normalize_identifier(dataset.manifest_name))
    if dataset.alias:
        keys.add(normalize_identifier(dataset.alias))
    if dataset.relation_name:
        keys.add(normalize_identifier(dataset.relation_name))
    keys.update(relation_fqn_lookup_keys(dataset.name))
    return frozenset(keys)
