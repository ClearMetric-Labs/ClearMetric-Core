#!/usr/bin/env python3
"""Regenerate adversarial expected.yaml from manifest.json."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import yaml
from clearmetric.graph import dataset_from_location
from clearmetric.lineage import build_lineage_map_from_project, load_project

DEFAULT_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "lineage"
    / "adversarial"
    / "enterprise_adversarial_manifest"
)


def refresh_expected(fixture_root: Path, *, dialect: str = "postgres") -> Path:
    manifest_path = fixture_root / "manifest.json"
    lineage_map = build_lineage_map_from_project(
        load_project(manifest_path, dialect=dialect),
        dialect=dialect,
    )
    edges = sorted(
        (edge.source_id, edge.target_id)
        for edge in lineage_map.edges
        if edge.kind == "derives_from"
    )
    warnings = Counter(
        (dataset_from_location(warning.location), warning.code)
        for warning in lineage_map.warnings
    )
    payload = {
        "dialect": dialect,
        "mode": "exact_edges",
        "derives_from": [[source, target] for source, target in edges],
        "warnings": [
            {"dataset": dataset, "code": code}
            for (dataset, code), _count in sorted(warnings.items())
            for _ in range(_count)
        ],
    }
    expected_path = fixture_root / "expected.yaml"
    expected_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return expected_path


def main() -> None:
    fixture_root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FIXTURE_ROOT
    expected_path = refresh_expected(fixture_root)
    edge_count = len(
        yaml.safe_load(expected_path.read_text(encoding="utf-8"))["derives_from"]
    )
    print(f"Wrote {expected_path} ({edge_count} edges)")


if __name__ == "__main__":
    main()
