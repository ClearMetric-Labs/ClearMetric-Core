"""Fixture case loading for committed lineage tests (seed/adversarial)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml
from clearmetric.graph import dataset_from_location
from clearmetric.lineage import build_lineage_map_from_project, load_project
from clearmetric.lineage.loaders import ProjectDataset
from clearmetric.lineage.models import LineageMap
from clearmetric.lineage.schema import resolve_type_overlay

VALID_CASE_KINDS = frozenset({"lineage_truth", "behavior_spec"})


def project_input_for_case(case_root: Path) -> Path:
    manifest = case_root / "manifest.json"
    return manifest if manifest.is_file() else case_root


def build_lineage_map_for_case(
    case_root: Path,
    *,
    dialect: str,
    project_input: Path | None = None,
) -> LineageMap:
    input_path = project_input or project_input_for_case(case_root)
    schema_path = case_root / "schema.json"
    if schema_path.is_file():
        raw = schema_path.read_text(encoding="utf-8").strip()
        if raw and raw != "{}":
            schema_overlay = resolve_type_overlay(schema_path, dialect=dialect)
            project = load_project(
                input_path,
                dialect=dialect,
                warehouse_overlay=schema_overlay,
            )
        else:
            project = load_project(input_path, dialect=dialect)
    else:
        project = load_project(input_path, dialect=dialect)
    return build_lineage_map_from_project(project, dialect=dialect)


def case_lineage_observation(
    case_root: Path,
    *,
    dialect: str,
    project_input: Path | None = None,
) -> tuple[set[tuple[str, str]], Counter[tuple[str, str]]]:
    lineage_map = build_lineage_map_for_case(
        case_root,
        dialect=dialect,
        project_input=project_input,
    )
    edges = {
        (edge.source_id, edge.target_id)
        for edge in lineage_map.edges
        if edge.kind == "derives_from"
    }
    warnings = Counter(
        (dataset_from_location(warning.location), warning.code)
        for warning in lineage_map.warnings
    )
    return edges, warnings


def load_forbidden_edges(case_root: Path) -> set[tuple[str, str]]:
    must_not_path = case_root / "must_not_edges.yaml"
    if not must_not_path.is_file():
        return set()
    payload = yaml.safe_load(must_not_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"must_not_edges.yaml must be an object: {must_not_path}")
    return {tuple(item) for item in payload.get("derives_from", [])}


def load_case_meta(case_root: Path, *, require_kind: bool = False) -> dict:
    meta_path = case_root / "meta.yaml"
    if not meta_path.is_file():
        raise ValueError(f"Missing meta.yaml: {case_root}")
    payload = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid meta.yaml: {meta_path}")
    if require_kind:
        case_kind = payload.get("case_kind")
        if case_kind not in VALID_CASE_KINDS:
            raise ValueError(
                f"Invalid or missing case_kind in {meta_path}: {case_kind!r}"
            )
    return payload


def resolve_downstream_model_id(
    case_root: Path,
    model: str,
    project_input: Path | None,
    *,
    meta: dict,
) -> str:
    manifest = project_input or project_input_for_case(case_root)
    dialect = str(meta.get("dialect") or "duckdb")
    project = load_project(manifest, dialect=dialect)
    dataset = find_local_dataset(project, model)
    if dataset is None:
        raise ValueError(f"Could not resolve downstream model {model!r} in {case_root}")
    return dataset.name


def find_local_dataset(project, model: str) -> ProjectDataset | None:
    if model in project.datasets:
        dataset = project.datasets[model]
        if dataset.kind == "local" and dataset.sql:
            return dataset
    for name, dataset in project.datasets.items():
        if dataset.kind != "local" or not dataset.sql:
            continue
        if name == model or name.endswith(f".{model}"):
            return dataset
    return None
