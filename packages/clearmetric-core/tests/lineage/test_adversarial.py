from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml
from clearmetric.graph import dataset_from_location

from .ground_truth import FIXTURES_ROOT, project_fixture_input
from .project_helpers import build_lineage_map

ADVERSARIAL_ROOT = FIXTURES_ROOT / "adversarial"


def _expected_files() -> list[Path]:
    return sorted(ADVERSARIAL_ROOT.glob("*/expected.yaml"))


@pytest.mark.parametrize(
    "expected_path", _expected_files(), ids=lambda path: path.parent.name
)
def test_adversarial_fixture_matches_expected_behavior(expected_path: Path):
    payload = yaml.safe_load(expected_path.read_text(encoding="utf-8"))
    dialect = payload["dialect"]
    mode = payload["mode"]
    case_root = expected_path.parent
    project_input = project_fixture_input(case_root)

    lineage_map = build_lineage_map(project_input, dialect=dialect)
    actual_edges = {
        (edge.source_id, edge.target_id)
        for edge in lineage_map.edges
        if edge.kind == "derives_from"
    }
    expected_edges = {tuple(item) for item in payload.get("derives_from", [])}

    actual_warnings = Counter(
        (dataset_from_location(warning.location), warning.code)
        for warning in lineage_map.warnings
    )
    expected_warnings = Counter(
        (item["dataset"], item["code"]) for item in payload.get("warnings", [])
    )

    if mode == "exact_edges":
        assert actual_edges == expected_edges
        assert actual_warnings == expected_warnings
        return

    if mode == "warnings":
        assert actual_edges == set()
        assert actual_warnings == expected_warnings
        return

    raise AssertionError(f"Unsupported adversarial mode {mode!r} in {expected_path}")
