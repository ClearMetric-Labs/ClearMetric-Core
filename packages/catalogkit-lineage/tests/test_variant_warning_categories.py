"""Variant warning categories: both warning and suppression halves."""

from __future__ import annotations

from collections import Counter

import pytest
import yaml
from catalogkit.lineage import build_lineage_map
from catalogkit.lineage.graph import (
    column_selection_from_id,
    derives_from_counts_by_source_dataset,
)

from .ground_truth import FIXTURES_ROOT, project_fixture_input

VARIANT_ROOT = FIXTURES_ROOT / "adversarial" / "enterprise_warning_variants"
SPEC_PATH = VARIANT_ROOT / "variant_expected_warnings.yml"
MANIFEST_PATH = VARIANT_ROOT / "manifest.json"


@pytest.fixture(name="variant_lineage")
def _variant_lineage():
    return build_lineage_map(project_fixture_input(MANIFEST_PATH), dialect="postgres")


@pytest.fixture(name="variant_spec")
def _variant_spec():
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


def _warnings_for_model(lineage_map, model_name: str) -> Counter[str]:
    return Counter(
        warning.code
        for warning in lineage_map.warnings
        if warning.location and model_name in warning.location
    )


def _derives_from_count(lineage_map, model_name: str) -> int:
    return derives_from_counts_by_source_dataset(lineage_map.edges).get(model_name, 0)


def _output_targets(lineage_map, model_name: str) -> set[str]:
    targets: set[str] = set()
    for edge in lineage_map.edges:
        if edge.kind != "derives_from":
            continue
        dataset_name, _column = column_selection_from_id(edge.source_id)
        if dataset_name != model_name:
            continue
        target_dataset, target_column = column_selection_from_id(edge.target_id)
        targets.add(f"{target_dataset}.{target_column}")
    return targets


def test_union_variants_warn_and_emit_zero_edges(variant_lineage, variant_spec):
    category = variant_spec["union_category"]
    expected_code = category["expected_warning"]
    for model_name in category["must_warn"]:
        warnings = _warnings_for_model(variant_lineage, model_name)
        assert warnings[expected_code] >= 1, model_name
        assert _derives_from_count(variant_lineage, model_name) == 0, model_name


def test_window_variants_warn_without_partition_order_value_edges(
    variant_lineage, variant_spec
):
    category = variant_spec["window_category"]
    allowed_codes = set(category["expected_warning_any_of"])
    forbidden = set(category["must_not_become_edges"])
    for model_name in category["must_warn"]:
        warnings = _warnings_for_model(variant_lineage, model_name)
        assert any(warnings[code] >= 1 for code in allowed_codes), model_name
        targets = _output_targets(variant_lineage, model_name)
        assert not (targets & forbidden), (model_name, targets & forbidden)


def test_quoted_variants_and_control(variant_lineage, variant_spec):
    category = variant_spec["quoted_ident_category"]
    expected_code = category["expected_warning"]
    for model_name in category["must_warn"]:
        warnings = _warnings_for_model(variant_lineage, model_name)
        assert warnings[expected_code] >= 1, model_name
        assert _derives_from_count(variant_lineage, model_name) == 0, model_name

    control = category["must_not_warn"][0]
    control_warnings = _warnings_for_model(variant_lineage, control)
    assert sum(control_warnings.values()) == 0, control
    assert (
        _derives_from_count(variant_lineage, control)
        == category["control_expected_edges"]
    )
