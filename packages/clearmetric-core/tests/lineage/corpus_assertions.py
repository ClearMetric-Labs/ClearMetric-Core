"""Shared assertion helpers for adversarial and local corpus cases."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml
from .fixture_cases import (
    case_lineage_observation,
    load_case_meta,
    load_forbidden_edges,
    resolve_downstream_model_id,
)
from .sql_oracle import assert_lineage_truth_sql_oracle


def load_case_payload(expected_path: Path) -> dict:
    payload = yaml.safe_load(expected_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected YAML must be an object: {expected_path}")
    return payload


def assert_case_matches(
    *,
    case_root: Path,
    expected_path: Path,
    project_loader,
) -> None:
    payload = load_case_payload(expected_path)
    dialect = payload["dialect"]
    mode = payload["mode"]
    project_input = project_loader(case_root)

    actual_edges, actual_warnings = case_lineage_observation(
        case_root,
        dialect=dialect,
        project_input=project_input,
    )
    expected_edges = {tuple(item) for item in payload.get("derives_from", [])}
    expected_warnings: Counter[tuple[str, str]] = Counter()
    for item in payload.get("warnings", []):
        expected_warnings[(item["dataset"], item["code"])] += int(
            item.get("count", 1)
        )

    forbidden_edges = load_forbidden_edges(case_root)
    assert not actual_edges & forbidden_edges, (
        f"Forbidden edges produced for {case_root.name}: "
        f"{sorted(actual_edges & forbidden_edges)}"
    )

    meta_path = case_root / "meta.yaml"
    meta = load_case_meta(case_root) if meta_path.is_file() else {}
    if meta.get("case_kind") == "lineage_truth":
        model = str(meta.get("model") or case_root.name)
        try:
            downstream = resolve_downstream_model_id(
                case_root,
                model,
                project_input,
                meta=meta,
            )
        except ValueError as exc:
            raise AssertionError(str(exc)) from exc
        assert_lineage_truth_sql_oracle(
            case_root=case_root,
            downstream_model=downstream,
            dialect=dialect,
            expected_edges=expected_edges,
        )

    if mode == "exact_edges":
        assert actual_edges == expected_edges
        if "warnings" in payload:
            assert actual_warnings == expected_warnings
        return

    if mode == "warnings":
        assert actual_edges == set()
        assert actual_warnings == expected_warnings
        return

    raise AssertionError(f"Unsupported mode {mode!r} in {expected_path}")
