from __future__ import annotations

import sys
from pathlib import Path

from catalogkit.lineage import build_catalog_artifact
from catalogkit.lineage.coverage import coverage_summary
from catalogkit.lineage.loaders import load_project

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sys.path.insert(0, str(PACKAGE_ROOT))
    from tests.ground_truth import project_inputs

    exit_code = 0
    for project_input, dialect in project_inputs():
        project_dir = (
            project_input.parent
            if project_input.name == "manifest.json"
            else project_input
        )
        project = load_project(project_input, dialect=dialect)
        artifact = build_catalog_artifact(project_input, dialect=dialect)
        summary = coverage_summary(artifact, project)
        print(
            f"{project_dir.name}: total={summary['total']} "
            f"resolved={summary['resolved']} flagged={summary['flagged']} "
            f"source_leaf={summary['source_leaf']} silent={summary['silent']} "
            f"bogus_source_leaves={summary['bogus_source_leaves']}"
        )
        print(f"  warning_counts={summary['warning_counts']}")
        if summary["silent"] or summary["bogus_source_leaves"]:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
