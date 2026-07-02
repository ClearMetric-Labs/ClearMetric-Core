from __future__ import annotations

from pathlib import Path

import pytest

from .corpus_assertions import assert_case_matches
from .ground_truth import FIXTURES_ROOT, project_fixture_input

SEED_ROOT = FIXTURES_ROOT / "seed"


def _expected_files() -> list[Path]:
    return sorted(SEED_ROOT.glob("*/expected.yaml"))


@pytest.mark.parametrize(
    "expected_path", _expected_files(), ids=lambda p: p.parent.name
)
def test_seed_fixture_matches_expected(expected_path: Path) -> None:
    assert_case_matches(
        case_root=expected_path.parent,
        expected_path=expected_path,
        project_loader=project_fixture_input,
    )
