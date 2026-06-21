#!/usr/bin/env python3
"""
Compare hand-derived value-lineage counts to per-model counts in expected.yaml.

THIS SCRIPT IS NOT CI SOURCE OF TRUTH.

It compares value_lineage_expected.yml (hand oracle) against per-model edge counts
read from expected.yaml — a snapshot of past engine output. If the live engine
drifts from its own snapshot, this script can still report green while the engine
is wrong.

For live validation, run:
  pytest tests/test_value_lineage_oracle.py
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml
from catalogkit.lineage.graph import column_selection_from_id

FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "adversarial"
    / "enterprise_adversarial_manifest"
)
HAND_PATH = FIXTURE_ROOT / "value_lineage_expected.yml"
EXPECTED_PATH = FIXTURE_ROOT / "expected.yaml"

CONVENTION_HINTS = {
    "select_star": "strict R6 expects 0 edges for bare *; qualified alias.* may retain partial edges",
    "ambiguous": "engine may count ambiguous col once, not once-per-source (adv_04)",
    "window": "engine may drop clean direct edges when window warns (adv_11)",
    "transitive": "engine may list transitive-to-source edges separately (adv_14)",
    "staging": "engine total may include source->staging edges (+14)",
}


def hand_count(model: dict) -> int:
    return int(model.get("edges_corrected", model.get("edges", 0)))


def snapshot_edge_counts(expected_path: Path = EXPECTED_PATH) -> dict[str, int]:
    payload = yaml.safe_load(expected_path.read_text(encoding="utf-8"))
    derives_from = payload.get("derives_from", [])
    if not isinstance(derives_from, list):
        raise ValueError(f"expected.yaml derives_from must be a list: {expected_path}")
    counts: Counter[str] = Counter()
    for edge in derives_from:
        if not isinstance(edge, list) or len(edge) != 2:
            raise ValueError(f"Invalid derives_from edge in {expected_path}: {edge!r}")
        source_id, _target_id = edge
        if not isinstance(source_id, str) or not source_id.startswith("column:"):
            raise ValueError(
                f"Invalid derives_from source id in {expected_path}: {source_id!r}"
            )
        dataset_name, _column_name = column_selection_from_id(source_id)
        counts[dataset_name] += 1
    return dict(counts)


def main() -> None:
    hand = yaml.safe_load(HAND_PATH.read_text(encoding="utf-8"))["models"]
    independent = {name: hand_count(model) for name, model in hand.items()}
    snapshot = snapshot_edge_counts()

    print("Independent hand-derived counts (from SQL, not engine):\n")
    width = max(len(name) for name in independent)
    for name in sorted(independent):
        print(f"  {name:<{width}}  {independent[name]}")
    total = sum(independent.values())
    print(f"\n  {'TOTAL (15 adversarial + 3 staging)':<{width}}  {total}")

    print("\n--- Per-model reconciliation (hand vs expected.yaml snapshot) ---")
    agree = 0
    for name in sorted(independent):
        expected = independent[name]
        actual = snapshot.get(name, 0)
        if expected == actual:
            print(f"  {name:<{width}}  {expected:>2} == {actual:<2}  agree")
            agree += 1
            continue
        hint = ""
        if "star" in name:
            hint = CONVENTION_HINTS["select_star"]
        elif "ambiguous" in name:
            hint = CONVENTION_HINTS["ambiguous"]
        elif "window" in name:
            hint = CONVENTION_HINTS["window"]
        elif name == "adv_14_rename_chain_downstream":
            hint = CONVENTION_HINTS["transitive"]
        print(f"  {name:<{width}}  hand={expected} engine={actual}  DIVERGE -> {hint}")

    print(f"\n{agree}/{len(independent)} models agree against snapshot.")
    print("For live engine validation: pytest tests/test_value_lineage_oracle.py")


if __name__ == "__main__":
    main()
