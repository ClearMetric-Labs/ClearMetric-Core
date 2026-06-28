from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

from _sqlglot_baseline import (
    build_raw_downstream_index,
    build_root_schema,
    build_sources_by_name,
    load_fixture,
)
from clearmetric.lineage import (
    build_catalog_artifact_from_project,
    load_project,
    trace_downstream_from_artifact,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
JAFFLE_MANIFEST = (
    WORKSPACE_ROOT
    / "packages"
    / "clearmetric-core"
    / "tests"
    / "fixtures"
    / "lineage"
    / "projects"
    / "jaffle_shop"
    / "manifest.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare clearmetric-core against baseline lineage approaches."
    )
    parser.add_argument("baseline", choices=("sqlglot", "dbt_manifest", "canva"))
    args = parser.parse_args()

    if args.baseline == "sqlglot":
        print(compare_sqlglot())
        return 0
    if args.baseline == "dbt_manifest":
        print(compare_dbt_manifest())
        return 0
    if args.baseline == "canva":
        print(compare_canva())
        return 0
    raise SystemExit(f"Unsupported baseline {args.baseline!r}")


def compare_sqlglot() -> str:
    _manifest, nodes, sql_by_name = load_fixture(JAFFLE_MANIFEST)
    sources_by_name = build_sources_by_name(nodes, sql_by_name)
    root_schema = build_root_schema(nodes)
    downstream_index = build_raw_downstream_index(
        nodes=nodes,
        sql_by_name=sql_by_name,
        root_schema=root_schema,
        sources_by_name=sources_by_name,
        dialect="postgres",
    )
    project = load_project(JAFFLE_MANIFEST, dialect="postgres")
    artifact = build_catalog_artifact_from_project(project, dialect="postgres")
    downstream_result = trace_downstream_from_artifact(
        artifact,
        selection="raw_payments.amount",
    ).related_ids

    manual_sources = len(sources_by_name)
    manual_schema_tables = len(root_schema)
    raw_downstream = downstream_index.get("raw_payments.amount", [])
    formatted_downstream = [item.removeprefix("column:") for item in downstream_result]

    lines = [
        "## sqlglot.lineage comparison",
        "",
        f"- Raw sqlglot baseline required manual assembly of `{manual_sources}` model SQL sources and `{manual_schema_tables}` root schema tables before asking one downstream impact question.",
        "- Raw sqlglot has no project-level downstream API; the baseline had to reverse-scan every modeled output column to answer `raw_payments.amount` impact.",
        f"- Raw sqlglot reverse scan returned: `{raw_downstream}`.",
        f"- `clearmetric-core` downstream traversal returned: `{formatted_downstream}`.",
        "- Differentiation: `clearmetric-core` owns project loading, reverse traversal, and stable `column:` IDs instead of leaving that glue to the caller.",
    ]
    return "\n".join(lines)


def compare_dbt_manifest() -> str:
    payload = json.loads(JAFFLE_MANIFEST.read_text(encoding="utf-8"))
    child_map = payload.get("child_map", {})
    queue = list(child_map.get("seed.jaffle_shop.raw_payments", []))
    descendants: list[str] = []
    seen: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        descendants.append(current)
        queue.extend(child_map.get(current, []))

    model_names = [item.split(".")[-1] for item in descendants]
    project = load_project(JAFFLE_MANIFEST, dialect="postgres")
    artifact = build_catalog_artifact_from_project(project, dialect="postgres")
    column_downstream = trace_downstream_from_artifact(
        artifact,
        selection="raw_payments.amount",
    ).related_ids
    formatted_columns = [item.removeprefix("column:") for item in column_downstream]

    lines = [
        "## dbt manifest lineage comparison",
        "",
        f"- `child_map` from the dbt manifest shows model-level descendants of `raw_payments`: `{model_names}`.",
        f"- `clearmetric-core` resolves the column-level blast radius for `raw_payments.amount`: `{formatted_columns}`.",
        "- Differentiation: dbt manifest lineage is model-level DAG metadata; `clearmetric-core` answers the pre-merge column impact question directly.",
    ]
    return "\n".join(lines)


def compare_canva() -> str:
    try:
        importlib.import_module("dbt_column_lineage_extractor")
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Canva comparison requires the optional dependency `dbt-column-lineage-extractor`. "
            "Install it in the active environment before running `compare_baselines.py canva`."
        ) from exc

    direct_help = subprocess.run(
        [sys.executable, "-m", "dbt_column_lineage_extractor.cli_direct", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    recursive_help = subprocess.run(
        [sys.executable, "-m", "dbt_column_lineage_extractor.cli_recursive", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    if direct_help.returncode != 0 or recursive_help.returncode != 0:
        raise SystemExit("Failed to inspect Canva extractor CLI entrypoints.")

    lines = [
        "## Canva extractor comparison",
        "",
        "- Canva's extractor exposes a two-step workflow: `dbt_column_lineage_direct` builds direct lineage JSON files from `--manifest` plus `--catalog`, then `dbt_column_lineage_recursive` answers one model+column query from those generated files.",
        "- `clearmetric-core` answers the same pre-merge downstream question in one headless command against a manifest or SQL folder, and emits a mergeable `CatalogArtifact` instead of tool-specific output files.",
        "- Concrete CLI difference from the installed package help:",
        f"  - direct command requires: `{_first_usage_line(direct_help.stdout)}`",
        f"  - recursive command requires: `{_first_usage_line(recursive_help.stdout)}`",
        "- Differentiation: Canva gives recursive column lineage and Mermaid output, but it does not replace ClearMetric Core's single-command impact traversal, shared artifact contract, or non-dbt SQL-folder path.",
    ]
    return "\n".join(lines)


def _first_usage_line(help_text: str) -> str:
    for line in help_text.splitlines():
        if line.startswith("usage: "):
            return line
    raise SystemExit("Expected a usage line in CLI help output.")


if __name__ == "__main__":
    raise SystemExit(main())
