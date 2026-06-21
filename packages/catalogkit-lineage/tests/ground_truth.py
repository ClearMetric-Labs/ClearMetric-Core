from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from catalogkit.core import CatalogArtifact
from catalogkit.lineage.build import (
    build_catalog_artifact_from_project,
    trace_downstream_from_artifact,
    trace_upstream_from_artifact,
)
from catalogkit.lineage.graph import dataset_from_location
from catalogkit.lineage.loaders import ProjectInput, load_project

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class WarningExpectation:
    code: str
    dataset: str
    subject_id: str | None = None


@dataclass(frozen=True)
class Probe:
    source_file: Path
    project_path: Path
    dialect: str
    selection: str
    expected_upstream: tuple[str, ...]
    expected_downstream: tuple[str, ...]
    expected_warnings: tuple[WarningExpectation, ...] = ()


@dataclass(frozen=True)
class ProbeResult:
    probe: Probe
    actual_upstream: tuple[str, ...]
    actual_downstream: tuple[str, ...]
    actual_warnings: tuple[WarningExpectation, ...]


def load_ground_truth_file(path: Path) -> list[Probe]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Ground-truth file must be a YAML object: {path}")

    project_path_value = _require_string(payload, "project_path", path=path)
    dialect = _require_string(payload, "dialect", path=path)
    probes_payload = payload.get("probes")
    if not isinstance(probes_payload, list) or not probes_payload:
        raise ValueError(
            f"Ground-truth file must define a non-empty probes list: {path}"
        )

    project_path = (path.parent / project_path_value).resolve()
    if not project_path.exists():
        raise FileNotFoundError(
            f"Ground-truth file references missing project fixture: {project_path}"
        )

    probes: list[Probe] = []
    for index, probe_payload in enumerate(probes_payload):
        if not isinstance(probe_payload, dict):
            raise ValueError(f"Probe #{index} must be a YAML object: {path}")
        probes.append(
            Probe(
                source_file=path,
                project_path=project_path,
                dialect=dialect,
                selection=_require_string(probe_payload, "selection", path=path),
                expected_upstream=_require_string_list(
                    probe_payload, "expected_upstream", path=path
                ),
                expected_downstream=_require_string_list(
                    probe_payload, "expected_downstream", path=path
                ),
                expected_warnings=_parse_warnings(
                    probe_payload.get("expected_warnings", []), path=path
                ),
            )
        )
    return probes


def run_probe(probe: Probe) -> ProbeResult:
    _project, artifact = load_built_fixture(probe.project_path, probe.dialect)
    upstream = trace_upstream_from_artifact(artifact, selection=probe.selection)
    downstream = trace_downstream_from_artifact(artifact, selection=probe.selection)
    actual_warnings = tuple(
        sorted(
            (
                WarningExpectation(
                    code=warning.code,
                    dataset=dataset_from_location(warning.location),
                    subject_id=warning.subject_id,
                )
                for warning in artifact.warnings
            ),
            key=lambda item: (item.dataset, item.code, item.subject_id or ""),
        )
    )
    return ProbeResult(
        probe=probe,
        actual_upstream=tuple(upstream.related_ids),
        actual_downstream=tuple(downstream.related_ids),
        actual_warnings=actual_warnings,
    )


def false_negatives(expected: tuple[str, ...], actual: tuple[str, ...]) -> set[str]:
    return set(expected) - set(actual)


def false_positives(expected: tuple[str, ...], actual: tuple[str, ...]) -> set[str]:
    return set(actual) - set(expected)


def diff_sets(*, expected: tuple[str, ...], actual: tuple[str, ...], label: str) -> str:
    missing = sorted(false_negatives(expected, actual))
    extras = sorted(false_positives(expected, actual))
    return (
        f"{label} mismatch\n"
        f"expected: {list(expected)}\n"
        f"actual:   {list(actual)}\n"
        f"missing:  {missing}\n"
        f"extras:   {extras}"
    )


def diff_warnings(
    *,
    expected: tuple[WarningExpectation, ...],
    actual: tuple[WarningExpectation, ...],
) -> str:
    expected_pairs = sorted(
        (item.dataset, item.code, item.subject_id) for item in expected
    )
    actual_pairs = sorted((item.dataset, item.code, item.subject_id) for item in actual)
    return f"warning mismatch\nexpected: {expected_pairs}\nactual:   {actual_pairs}"


def _parse_warnings(payload: object, *, path: Path) -> tuple[WarningExpectation, ...]:
    if not isinstance(payload, list):
        raise ValueError(f"expected_warnings must be a list: {path}")
    warnings: list[WarningExpectation] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"expected_warnings[{index}] must be an object: {path}")
        warnings.append(
            WarningExpectation(
                code=_require_string(item, "code", path=path),
                dataset=_require_string(item, "dataset", path=path),
                subject_id=(
                    _require_string(item, "subject_id", path=path)
                    if "subject_id" in item
                    else None
                ),
            )
        )
    return tuple(warnings)


def project_dialects() -> dict[Path, str]:
    dialects: dict[Path, str] = {}
    for path in sorted((FIXTURES_ROOT / "ground_truth").glob("*.yaml")):
        probes = load_ground_truth_file(path)
        if not probes:
            continue
        dialects[probes[0].project_path.resolve()] = probes[0].dialect
    return dialects


def project_fixture_input(root: Path) -> Path:
    """Return manifest.json when present, otherwise the fixture directory."""
    manifest = root / "manifest.json"
    return manifest if manifest.exists() else root


def project_inputs() -> list[tuple[Path, str]]:
    """Return project fixture paths paired with dialects from ground-truth YAML."""
    dialects = project_dialects()
    inputs: list[tuple[Path, str]] = []
    for project_dir in sorted((FIXTURES_ROOT / "projects").iterdir()):
        if not project_dir.is_dir():
            continue
        project_input = project_fixture_input(project_dir)
        dialect = dialects.get(project_input.resolve())
        if dialect is None:
            raise ValueError(
                f"Missing ground-truth dialect mapping for fixture: {project_input}"
            )
        inputs.append((project_input, dialect))
    return inputs


@lru_cache(maxsize=None)
def load_built_fixture(
    project_path: Path, dialect: str
) -> tuple[ProjectInput, CatalogArtifact]:
    """Load and build a fixture once per process for test reuse."""
    project = load_project(project_path.resolve(), dialect=dialect)
    artifact = build_catalog_artifact_from_project(project, dialect=dialect)
    return project, artifact


@lru_cache(maxsize=1)
def coverage_baselines() -> dict[str, dict[str, object]]:
    baseline_path = FIXTURES_ROOT / "coverage_baseline.yaml"
    payload = yaml.safe_load(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Coverage baseline must be a mapping: {baseline_path}")
    return payload


def _require_string(payload: dict, key: str, *, path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Expected non-empty string for {key!r}: {path}")
    return value.strip()


def _require_string_list(payload: dict, key: str, *, path: Path) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Expected list for {key!r}: {path}")
    items = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"Expected non-empty string entries in {key!r}: {path}")
        items.append(entry.strip())
    return tuple(items)
