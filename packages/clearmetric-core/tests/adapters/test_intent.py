"""Intent adapter tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from clearmetric.adapters.intent import ingest_intent
from clearmetric.core.errors import AdapterError
from clearmetric.core.project import (
    ClearMetricProject,
    IntentSource,
    PolicyConfig,
    ProjectSources,
)


def _project(tmp_path: Path, intent_dir: Path) -> ClearMetricProject:
    return ClearMetricProject(
        version=1,
        dialect="postgres",
        sources=ProjectSources(intent=IntentSource(paths=[str(intent_dir)])),
        posture="strict",
        policy=PolicyConfig(rules=str(tmp_path / "rules.yaml")),
    )


def test_intent_ingest_builds_metric_and_query_nodes(tmp_path: Path):
    intent_dir = tmp_path / "intent"
    intent_dir.mkdir()
    (intent_dir / "metrics.yaml").write_text(
        yaml.safe_dump(
            {
                "metrics": [
                    {
                        "id": "revenue",
                        "name": "Revenue",
                        "formula": "sum(amount)",
                        "depends_on": ["column:orders.amount"],
                    }
                ],
                "queries": [
                    {
                        "id": "top_orders",
                        "name": "Top Orders",
                        "sql": "SELECT 1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    rules = tmp_path / "rules.yaml"
    rules.write_text("rules: []\n", encoding="utf-8")

    artifact = ingest_intent(_project(tmp_path, intent_dir))
    kinds = {node.kind for node in artifact.nodes}
    assert kinds == {"metric", "query"}


def test_intent_batch_validation_lists_all_errors(tmp_path: Path):
    intent_dir = tmp_path / "intent"
    intent_dir.mkdir()
    (intent_dir / "bad.yaml").write_text("metrics:\n  - name: missing-id\n", encoding="utf-8")
    (intent_dir / "also_bad.yaml").write_text("queries:\n  - id: q1\n", encoding="utf-8")
    rules = tmp_path / "rules.yaml"
    rules.write_text("rules: []\n", encoding="utf-8")

    with pytest.raises(AdapterError) as exc:
        ingest_intent(_project(tmp_path, intent_dir))
    message = str(exc.value)
    assert "bad.yaml" in message
    assert "also_bad.yaml" in message
