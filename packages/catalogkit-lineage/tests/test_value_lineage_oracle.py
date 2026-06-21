"""Live-engine checks against the hand-derived value-lineage oracle."""

from __future__ import annotations

from functools import lru_cache

import yaml
from catalogkit.lineage import build_lineage_map
from catalogkit.lineage.graph import (
    column_selection_from_id,
    derives_from_counts_by_source_dataset,
)

from .ground_truth import FIXTURES_ROOT, project_fixture_input

ORACLE_PATH = (
    FIXTURES_ROOT
    / "adversarial"
    / "enterprise_adversarial_manifest"
    / "value_lineage_expected.yml"
)
MANIFEST_PATH = ORACLE_PATH.parent / "manifest.json"


@lru_cache(maxsize=1)
def _enterprise_lineage_map():
    return build_lineage_map(project_fixture_input(MANIFEST_PATH), dialect="postgres")


def _hand_edge_counts() -> dict[str, int]:
    payload = yaml.safe_load(ORACLE_PATH.read_text(encoding="utf-8"))
    models = payload["models"]
    return {
        name: int(model.get("edges_corrected", model.get("edges", 0)))
        for name, model in models.items()
    }


def _engine_edge_counts(lineage_map) -> dict[str, int]:
    return derives_from_counts_by_source_dataset(lineage_map.edges)


def _engine_edges_for_model(lineage_map, model_name: str) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for edge in lineage_map.edges:
        if edge.kind != "derives_from":
            continue
        source_dataset, source_column = column_selection_from_id(edge.source_id)
        if source_dataset != model_name:
            continue
        target_dataset, target_column = column_selection_from_id(edge.target_id)
        edges.add((source_column, f"{target_dataset}.{target_column}"))
    return edges


def test_hand_oracle_per_model_edge_counts_match_live_engine():
    hand_counts = _hand_edge_counts()
    engine_counts = _engine_edge_counts(_enterprise_lineage_map())
    divergent = []
    for model_name, expected_count in sorted(hand_counts.items()):
        actual_count = engine_counts.get(model_name, 0)
        if actual_count != expected_count:
            divergent.append((model_name, expected_count, actual_count))
    assert not divergent, "Hand oracle diverges from live engine: " + ", ".join(
        f"{name} expected={exp} actual={act}" for name, exp, act in divergent
    )


def test_adv_11_window_value_edges_and_no_partition_order_leakage():
    """Earn the no-over-warning claim: 3 value edges, window controls excluded."""
    lineage_map = _enterprise_lineage_map()
    engine_counts = _engine_edge_counts(lineage_map)
    assert engine_counts.get("adv_11_window") == 3

    edges = _engine_edges_for_model(lineage_map, "adv_11_window")
    assert ("running_total", "stg_a.amount") in edges
    assert ("id", "stg_a.id") in edges
    assert ("user_id", "stg_a.user_id") in edges

    output_columns = {output_column for output_column, _target in edges}
    assert "created_at" not in output_columns
    assert "row_num" not in output_columns
