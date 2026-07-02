from __future__ import annotations

import pytest
from clearmetric.lineage.errors import LineageInputError
from clearmetric.lineage.loaders import ProjectDataset, ProjectInput
from clearmetric.lineage.relations import (
    is_canonical_relation_id,
    normalize_relation_id,
    parse_canonical_relation_id,
)
from clearmetric.lineage.schema_registry import RelationSchema, SchemaRegistry


def test_ambiguous_alias_key_is_not_bound() -> None:
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="collision",
        datasets={
            "db.one.alpha": ProjectDataset(
                name="db.one.alpha",
                kind="local",
                sql="select 1",
                dependency_names=(),
                declared_columns=("id",),
                evidence_file=None,
                alias="shared",
            ),
            "db.two.beta": ProjectDataset(
                name="db.two.beta",
                kind="local",
                sql="select 1",
                dependency_names=(),
                declared_columns=("id",),
                evidence_file=None,
                alias="shared",
            ),
        },
    )
    registry = SchemaRegistry.from_project(project, dialect="duckdb")
    assert "shared" not in registry._alias_index


def _sample_project() -> ProjectInput:
    fq = "clearmetric_tuva_offline.input_layer.input_layer__condition"
    short = "input_layer__condition"
    return ProjectInput(
        input_kind="dbt_manifest",
        label="tuva",
        datasets={
            fq: ProjectDataset(
                name=fq,
                kind="local",
                sql="select 1",
                dependency_names=(),
                declared_columns=("condition_id",),
                evidence_file=None,
                alias=short,
                manifest_name=short,
                database="clearmetric_tuva_offline",
                schema_name="input_layer",
            )
        },
    )


def test_parse_canonical_relation_id() -> None:
    parts = parse_canonical_relation_id(
        "clearmetric_tuva_offline.core._stg_clinical_condition"
    )
    assert parts.database == "clearmetric_tuva_offline"
    assert parts.schema == "core"
    assert parts.model == "_stg_clinical_condition"


def test_normalize_short_name_to_canonical() -> None:
    project = _sample_project()
    resolved = normalize_relation_id("input_layer__condition", project=project)
    assert resolved == "clearmetric_tuva_offline.input_layer.input_layer__condition"
    assert is_canonical_relation_id(resolved)


def test_ambiguous_short_name_raises() -> None:
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="x",
        datasets={
            "db.s.a": ProjectDataset(
                name="db.s.a",
                kind="local",
                sql=None,
                dependency_names=(),
                declared_columns=(),
                evidence_file=None,
                alias="dup",
            ),
            "db.s.b": ProjectDataset(
                name="db.s.b",
                kind="local",
                sql=None,
                dependency_names=(),
                declared_columns=(),
                evidence_file=None,
                alias="dup",
            ),
        },
    )
    with pytest.raises(LineageInputError, match="Ambiguous"):
        normalize_relation_id("dup", project=project)


def test_tuva_schema_key_aliases_resolve_through_registry() -> None:
    canonical = "clearmetric_tuva_offline.terminology.icd_10_cm"
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="tuva",
        datasets={
            canonical: ProjectDataset(
                name=canonical,
                kind="root",
                sql=None,
                dependency_names=(),
                declared_columns=("icd_10_cm", "long_description"),
                evidence_file=None,
                alias="icd_10_cm",
                manifest_name="icd_10_cm",
                database="clearmetric_tuva_offline",
                schema_name="terminology",
            ),
            "clearmetric_tuva_offline.reference_data.calendar": ProjectDataset(
                name="clearmetric_tuva_offline.reference_data.calendar",
                kind="root",
                sql=None,
                dependency_names=(),
                declared_columns=("year", "month"),
                evidence_file=None,
                alias="calendar",
                manifest_name="calendar",
                database="clearmetric_tuva_offline",
                schema_name="reference_data",
            ),
        },
    )

    registry = SchemaRegistry.from_project(project, dialect="duckdb")

    resolved_fqn = registry.resolve_relation(canonical)
    resolved_schema_table = registry.resolve_relation("terminology.icd_10_cm")
    resolved_short = registry.resolve_relation("icd_10_cm")
    resolved_calendar = registry.resolve_relation("reference_data.calendar")

    assert isinstance(resolved_fqn, RelationSchema)
    assert resolved_fqn.relation_id == canonical
    assert isinstance(resolved_schema_table, RelationSchema)
    assert resolved_schema_table.relation_id == canonical
    assert isinstance(resolved_short, RelationSchema)
    assert resolved_short.relation_id == canonical
    assert isinstance(resolved_calendar, RelationSchema)
    assert (
        resolved_calendar.relation_id
        == "clearmetric_tuva_offline.reference_data.calendar"
    )
