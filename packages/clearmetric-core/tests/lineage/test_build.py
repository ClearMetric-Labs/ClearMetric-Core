from __future__ import annotations

from pathlib import Path

from clearmetric.emitters.openlineage import build_openlineage_payload
from clearmetric.lineage.build import edges_by_model_from_project
from clearmetric.lineage.loaders import ProjectDataset, ProjectInput

from .project_helpers import (
    build_catalog_artifact,
    build_lineage_map,
    trace_downstream,
)


def _example_root() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "lineage"
        / "projects"
        / "jaffle_shop"
    )


def _folder_example_root() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "lineage"
        / "projects"
        / "sql_folder"
    )


def test_build_lineage_map_from_manifest():
    manifest_path = _example_root() / "manifest.json"

    lineage_map = build_lineage_map(manifest_path, dialect="postgres")

    assert lineage_map.summary.input_kind == "dbt_manifest"
    assert lineage_map.summary.dataset_count >= 8
    assert lineage_map.summary.column_count >= 20


def test_folder_input_builds_successfully():
    compiled_dir = _folder_example_root()

    lineage_map = build_lineage_map(compiled_dir, dialect="postgres")

    assert lineage_map.summary.input_kind == "sql_folder"
    assert lineage_map.warnings == []


def test_openlineage_export_contains_column_lineage_entries():
    compiled_dir = _folder_example_root()
    artifact = build_catalog_artifact(compiled_dir, dialect="postgres")

    payload = build_openlineage_payload(artifact, job_name="sql_folder")

    assert payload["job"]["name"] == "sql_folder"
    assert any(entry["name"] == "orders_base" for entry in payload["datasets"])
    assert any(
        entry["dataset"] == "orders_base" and entry["column"] == "amount"
        for entry in payload["columnLineage"]
    )


def test_openlineage_export_groups_multiple_inputs_per_output_column(tmp_path: Path):
    report_sql = tmp_path / "report.sql"
    report_sql.write_text(
        """
        select
            source_a.amount + source_b.amount as total_amount
        from source_a
        join source_b
            on source_a.id = source_b.id
        """.strip(),
        encoding="utf-8",
    )

    payload = build_openlineage_payload(
        build_catalog_artifact(report_sql.parent, dialect="postgres"),
        job_name=report_sql.parent.name,
    )

    grouped_entries = [
        entry
        for entry in payload["columnLineage"]
        if entry["dataset"] == "report" and entry["column"] == "total_amount"
    ]

    assert len(grouped_entries) == 1
    assert grouped_entries[0]["inputFields"] == [
        {"namespace": "clearmetric", "name": "source_a", "field": "amount"},
        {"namespace": "clearmetric", "name": "source_b", "field": "amount"},
    ]


def _star_project(sql: str) -> ProjectInput:
    return ProjectInput(
        input_kind="dbt_manifest",
        label="star",
        datasets={
            "db.raw.people": ProjectDataset(
                name="db.raw.people",
                kind="root",
                sql=None,
                dependency_names=(),
                declared_columns=("id", "name"),
                evidence_file=None,
            ),
            "db.core.patient": ProjectDataset(
                name="db.core.patient",
                kind="local",
                sql=sql,
                dependency_names=("db.raw.people",),
                declared_columns=("id", "name"),
                evidence_file=None,
            ),
        },
    )


def test_registry_proven_single_relation_star_emits_names_only_edges() -> None:
    result = edges_by_model_from_project(
        _star_project("select * from db.raw.people"),
        dialect="duckdb",
    )["db.core.patient"]

    assert result.edges == frozenset(
        {
            ("db.raw.people", "id", "db.core.patient", "id"),
            ("db.raw.people", "name", "db.core.patient", "name"),
        }
    )


def test_alias_qualified_star_remains_suppressed_for_existing_adversarial_contract() -> None:
    result = edges_by_model_from_project(
        _star_project("select p.* from db.raw.people as p"),
        dialect="duckdb",
    )["db.core.patient"]

    assert result.edges == frozenset()


def test_ambiguous_multi_relation_star_emits_no_edges() -> None:
    project = _star_project(
        "select * from db.raw.people join db.raw.people as other on people.id = other.id"
    )
    result = edges_by_model_from_project(project, dialect="duckdb")["db.core.patient"]

    assert result.edges == frozenset()


def test_jaffle_star_models_suppress_column_lineage():
    manifest_path = _example_root() / "manifest.json"

    lineage_map = build_lineage_map(manifest_path, dialect="postgres")

    assert not any(edge.kind == "derives_from" for edge in lineage_map.edges)
    assert any(warning.code == "select_star" for warning in lineage_map.warnings)

    payment_method_downstream = trace_downstream(
        manifest_path,
        dialect="postgres",
        selection="raw_payments.payment_method",
    )

    assert payment_method_downstream.related_ids == []


def test_openlineage_export_accepts_prebuilt_artifact():
    manifest_path = _example_root() / "manifest.json"
    artifact = build_catalog_artifact(manifest_path, dialect="postgres")

    payload = build_openlineage_payload(artifact)

    assert payload["datasets"]
    assert payload["job"]["name"] == "clearmetric"
    assert isinstance(payload["columnLineage"], list)


def _dataset(
    name: str,
    *,
    kind: str = "root",
    sql: str | None = None,
    deps: tuple[str, ...] = (),
    columns: tuple[str, ...] = ("id",),
) -> ProjectDataset:
    return ProjectDataset(
        name=name,
        kind=kind,
        sql=sql,
        dependency_names=deps,
        declared_columns=columns,
        evidence_file=None,
    )


def test_patient_style_union_emits_both_branch_upstreams() -> None:
    claims = "db.claims_preprocessing.normalized_eligibility_remove_duplicates"
    clinical = "db.core.int_patient_remove_duplicates"
    patient_sql = f"""
with claims_patient as (
    select person_id, first_name from {claims}
),
clinical_patient as (
    select person_id, first_name from {clinical}
),
unioned as (
    select claims_patient.person_id, claims_patient.first_name from claims_patient
    union all
    select clinical_patient.person_id, clinical_patient.first_name from clinical_patient
)
select person_id, first_name from unioned
""".strip()
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="patient_union",
        datasets={
            claims: _dataset(claims, columns=("person_id", "first_name")),
            clinical: _dataset(clinical, columns=("person_id", "first_name")),
            "db.core.patient": _dataset(
                "db.core.patient",
                kind="local",
                sql=patient_sql,
                deps=(clinical,),
                columns=("person_id", "first_name"),
            ),
        },
    )
    result = edges_by_model_from_project(project, dialect="duckdb")["db.core.patient"]
    assert (claims, "person_id", "db.core.patient", "person_id") in result.edges
    assert (claims, "first_name", "db.core.patient", "first_name") in result.edges
    assert (clinical, "person_id", "db.core.patient", "person_id") in result.edges
    assert (clinical, "first_name", "db.core.patient", "first_name") in result.edges


def test_multi_branch_encounter_union_emits_grain_upstreams() -> None:
    grain_a = "db.claims_preprocessing.office_visit__encounter_grain"
    grain_b = "db.claims_preprocessing.lab__encounter_grain"
    person_src = "db.claims_preprocessing.encounters__patient_data_source_id"
    sql = f"""
with office_visit as (
    select data_source, person_id from {grain_a}
),
lab as (
    select data_source, person_id from {grain_b}
),
unioned as (
    select office_visit.data_source, office_visit.person_id from office_visit
    union all
    select lab.data_source, lab.person_id from lab
)
select data_source, person_id from unioned
""".strip()
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="encounter_union",
        datasets={
            grain_a: _dataset(grain_a, columns=("data_source", "person_id")),
            grain_b: _dataset(grain_b, columns=("data_source", "person_id")),
            person_src: _dataset(person_src, columns=("person_id",)),
            "db.core._stg_claims_encounter": _dataset(
                "db.core._stg_claims_encounter",
                kind="local",
                sql=sql,
                deps=(grain_a, grain_b, person_src),
                columns=("data_source", "person_id"),
            ),
        },
    )
    result = edges_by_model_from_project(project, dialect="duckdb")[
        "db.core._stg_claims_encounter"
    ]
    assert (grain_a, "data_source", "db.core._stg_claims_encounter", "data_source") in result.edges
    assert (grain_b, "data_source", "db.core._stg_claims_encounter", "data_source") in result.edges
    upstream_tables = {edge[0] for edge in result.edges}
    assert "enc" not in upstream_tables
    assert "office_visit" not in upstream_tables


def test_macro_generated_union_star_branches_emit_schema_upstreams() -> None:
    grain_a = "db.claims_preprocessing.office_visit__encounter_grain"
    grain_b = "db.claims_preprocessing.lab__encounter_grain"
    grain_c = "db.claims_preprocessing.dme__encounter_grain"
    sql = f"""
with base as (
    (select cast('{grain_a}' as TEXT) as _dbt_source_relation, * from {grain_a})
    union all
    (select cast('{grain_b}' as TEXT) as _dbt_source_relation, * from {grain_b})
    union all
    (select cast('{grain_c}' as TEXT) as _dbt_source_relation, * from {grain_c})
)
select cast(base.data_source as TEXT) as data_source from base
""".strip()
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="macro_union_encounter",
        datasets={
            grain_a: _dataset(grain_a, columns=("data_source", "encounter_id")),
            grain_b: _dataset(grain_b, columns=("data_source", "encounter_id")),
            grain_c: _dataset(grain_c, columns=("data_source", "encounter_id")),
            "db.core._stg_claims_encounter": _dataset(
                "db.core._stg_claims_encounter",
                kind="local",
                sql=sql,
                deps=(grain_a, grain_b, grain_c),
                columns=("data_source",),
            ),
        },
    )
    result = edges_by_model_from_project(project, dialect="duckdb")[
        "db.core._stg_claims_encounter"
    ]
    for grain in (grain_a, grain_b, grain_c):
        assert (grain, "data_source", "db.core._stg_claims_encounter", "data_source") in result.edges


def test_clinical_encounter_emits_canonical_upstream_not_cte_alias() -> None:
    encounter = "db.input_layer.input_layer__encounter"
    terminology = "db.terminology.admit_type"
    sql = f"""
with enc as (
    select
        encounter_id,
        admit_type_code
    from {encounter} as encounter
)
select
    enc.encounter_id,
    enc.admit_type_code,
    admit_type.admit_type_description
from enc
left join {terminology} as admit_type
    on enc.admit_type_code = admit_type.admit_type_code
""".strip()
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="clinical_encounter",
        datasets={
            encounter: _dataset(
                encounter,
                columns=("encounter_id", "admit_type_code"),
            ),
            terminology: _dataset(
                terminology,
                columns=("admit_type_code", "admit_type_description"),
            ),
            "db.core._stg_clinical_encounter": _dataset(
                "db.core._stg_clinical_encounter",
                kind="local",
                sql=sql,
                deps=(encounter, terminology),
                columns=("encounter_id", "admit_type_code", "admit_type_description"),
            ),
        },
    )
    result = edges_by_model_from_project(project, dialect="duckdb")[
        "db.core._stg_clinical_encounter"
    ]
    upstream_tables = {edge[0] for edge in result.edges}
    assert "enc" not in upstream_tables
    assert "encounter" not in upstream_tables
    assert (encounter, "encounter_id", "db.core._stg_clinical_encounter", "encounter_id") in result.edges
    assert (
        terminology,
        "admit_type_description",
        "db.core._stg_clinical_encounter",
        "admit_type_description",
    ) in result.edges


def test_case_when_null_checks_emit_terminology_lineage_for_code_metadata() -> None:
    icd = "db.terminology.icd_10_cm"
    obs = "db.core._stg_clinical_observation"
    sql = f"""
select
  case when icd10cm.icd_10_cm is not null then 'icd-10-cm' end as normalized_code_type,
  case when coalesce(icd10cm.icd_10_cm) is not null then 'automatic' end as mapping_method,
  coalesce(icd10cm.icd_10_cm) as normalized_code
from {obs} as obs
left join {icd} as icd10cm on true
""".strip()
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="observation_code_metadata",
        datasets={
            obs: _dataset(obs, columns=("source_code",)),
            icd: _dataset(icd, columns=("icd_10_cm",)),
            "db.core.observation": _dataset(
                "db.core.observation",
                kind="local",
                sql=sql,
                deps=(obs, icd),
                columns=("normalized_code_type", "mapping_method", "normalized_code"),
            ),
        },
    )
    result = edges_by_model_from_project(project, dialect="duckdb")["db.core.observation"]
    assert (
        icd,
        "icd_10_cm",
        "db.core.observation",
        "normalized_code_type",
    ) in result.edges
    assert (icd, "icd_10_cm", "db.core.observation", "mapping_method") in result.edges
    assert (icd, "icd_10_cm", "db.core.observation", "normalized_code") in result.edges


def test_explicit_select_with_bare_star_still_emits_non_star_outputs() -> None:
    calendar = "db.reference_data.calendar"
    elig = "db.claims_preprocessing.normalized_eligibility"
    sql = f"""
with month_dates as (
  select year || right('0' || month, 2) as year_month
  from {calendar}
  group by year, month
),
joined as (
  select a.person_id, b.year_month
  from {elig} as a
  inner join month_dates as b on true
)
select
  cast(md5(cast(person_id as TEXT) || '-' || cast(year_month as TEXT)) as TEXT) as member_month_key,
  year_month
from joined
""".strip()
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="member_months_star_mix",
        datasets={
            calendar: _dataset(calendar, columns=("year", "month")),
            elig: _dataset(
                elig,
                kind="local",
                sql="select person_id from input",
                deps=(),
                columns=("person_id",),
            ),
            "db.core._int_member_months": _dataset(
                "db.core._int_member_months",
                kind="local",
                sql=sql,
                deps=(calendar, elig),
                columns=("member_month_key", "year_month"),
            ),
        },
    )
    result = edges_by_model_from_project(project, dialect="duckdb")[
        "db.core._int_member_months"
    ]
    assert (
        calendar,
        "year",
        "db.core._int_member_months",
        "member_month_key",
    ) in result.edges
    assert (
        calendar,
        "month",
        "db.core._int_member_months",
        "member_month_key",
    ) in result.edges
    assert (
        calendar,
        "year",
        "db.core._int_member_months",
        "year_month",
    ) in result.edges
    assert (
        calendar,
        "month",
        "db.core._int_member_months",
        "year_month",
    ) in result.edges
    assert not any(
        edge[0] == elig and edge[1] == "person_id" and edge[3] == "member_month_key"
        for edge in result.edges
    )


def test_inner_cte_qualified_star_does_not_block_outer_provider_lineage() -> None:
    provider = "db.provider_data.provider"
    med = "db.core._stg_claims_medical_claim"
    sql = f"""
with provider as (
  select aa.*
  from {provider} as aa
  inner join (select distinct facility_npi as npi from {med}) as bb
    on aa.npi = bb.npi
)
select
  cast(npi as TEXT) as location_id,
  cast(provider_organization_name as TEXT) as name
from provider
""".strip()
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="claims_location",
        datasets={
            provider: _dataset(
                provider,
                columns=("npi", "provider_organization_name"),
            ),
            med: _dataset(
                med,
                kind="local",
                sql="select 1 as facility_npi",
                columns=("facility_npi",),
            ),
            "db.core._stg_claims_location": _dataset(
                "db.core._stg_claims_location",
                kind="local",
                sql=sql,
                deps=(med, provider),
                columns=("location_id", "name"),
            ),
        },
    )
    result = edges_by_model_from_project(project, dialect="duckdb")[
        "db.core._stg_claims_location"
    ]
    assert (
        provider,
        "npi",
        "db.core._stg_claims_location",
        "location_id",
    ) in result.edges
    assert (
        provider,
        "provider_organization_name",
        "db.core._stg_claims_location",
        "name",
    ) in result.edges


def test_union_null_padding_prefers_final_branch() -> None:
    input_layer = "db.input_layer.input_layer__patient"
    eligibility = "db.claims_preprocessing.normalized_eligibility"
    sql = f"""
select distinct
    cast(person_id as TEXT) as person_id,
    cast(null as TEXT) as patient_id,
    cast(member_id as TEXT) as member_id,
    cast(payer as TEXT) as payer,
    cast(plan as TEXT) as plan,
    cast(data_source as TEXT) as data_source
from {eligibility}
union all
select distinct
    cast(person_id as TEXT) as person_id,
    cast(patient_id as TEXT) as patient_id,
    cast(null as TEXT) as member_id,
    cast(null as TEXT) as payer,
    cast(null as TEXT) as plan,
    cast(data_source as TEXT) as data_source
from {input_layer}
""".strip()
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="union_null_padding",
        datasets={
            input_layer: _dataset(
                input_layer,
                columns=("data_source", "patient_id", "person_id"),
            ),
            eligibility: _dataset(
                eligibility,
                columns=("data_source", "person_id", "member_id", "payer", "plan"),
            ),
            "db.core.person_id_crosswalk": _dataset(
                "db.core.person_id_crosswalk",
                kind="local",
                sql=sql,
                deps=(input_layer, eligibility),
                columns=(
                    "data_source",
                    "patient_id",
                    "person_id",
                    "member_id",
                    "payer",
                    "plan",
                ),
            ),
        },
    )
    result = edges_by_model_from_project(project, dialect="duckdb")[
        "db.core.person_id_crosswalk"
    ]
    assert (
        input_layer,
        "data_source",
        "db.core.person_id_crosswalk",
        "data_source",
    ) in result.edges
    assert (
        input_layer,
        "patient_id",
        "db.core.person_id_crosswalk",
        "patient_id",
    ) in result.edges
    assert (
        input_layer,
        "person_id",
        "db.core.person_id_crosswalk",
        "person_id",
    ) in result.edges
    assert not any(
        edge[3] in {"member_id", "payer", "plan"} for edge in result.edges
    )


def test_case_expression_emits_audit_column_lineage() -> None:
    upstream = "db.claims_preprocessing.normalized_eligibility_remove_duplicates"
    sql = f"""
select
    case
        when {upstream}.tuva_last_run is null then 'unknown'
        else 'adult'
    end as age_group
from {upstream}
""".strip()
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="case_age_group",
        datasets={
            upstream: _dataset(
                upstream,
                columns=("tuva_last_run",),
            ),
            "db.core.patient": _dataset(
                "db.core.patient",
                kind="local",
                sql=sql,
                deps=(upstream,),
                columns=("age_group",),
            ),
        },
    )
    result = edges_by_model_from_project(project, dialect="duckdb")["db.core.patient"]
    assert (
        upstream,
        "tuva_last_run",
        "db.core.patient",
        "age_group",
    ) in result.edges


def test_cast_passthrough_from_preprocessing_parent() -> None:
    parent = "db.claims_preprocessing.normalized_input_pharmacy_claim"
    sql = f"""
select
    cast(dispensing_provider_name as TEXT) as dispensing_provider_name,
    cast(prescribing_provider_name as TEXT) as prescribing_provider_name
from {parent}
""".strip()
    project = ProjectInput(
        input_kind="dbt_manifest",
        label="pharmacy_passthrough",
        datasets={
            parent: _dataset(
                parent,
                columns=("dispensing_provider_name", "prescribing_provider_name"),
            ),
            "db.core._stg_claims_pharmacy_claim": _dataset(
                "db.core._stg_claims_pharmacy_claim",
                kind="local",
                sql=sql,
                deps=(parent,),
                columns=("dispensing_provider_name", "prescribing_provider_name"),
            ),
        },
    )
    result = edges_by_model_from_project(project, dialect="duckdb")[
        "db.core._stg_claims_pharmacy_claim"
    ]
    assert (
        parent,
        "dispensing_provider_name",
        "db.core._stg_claims_pharmacy_claim",
        "dispensing_provider_name",
    ) in result.edges
    assert (
        parent,
        "prescribing_provider_name",
        "db.core._stg_claims_pharmacy_claim",
        "prescribing_provider_name",
    ) in result.edges
