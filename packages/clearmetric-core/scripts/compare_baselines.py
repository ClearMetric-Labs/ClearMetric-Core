from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

from _sqlglot_baseline import build_raw_downstream_index
from clearmetric.graph import trace_downstream_from_artifact
from clearmetric.lineage import build_catalog_artifact_from_project, load_project

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
    parser.add_argument(
        "baseline",
        choices=("sqlglot", "dbt_manifest", "canva"),
    )
    parser.add_argument("--manifest", type=Path, default=JAFFLE_MANIFEST)
    parser.add_argument("--dialect", default="postgres")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    if args.baseline == "sqlglot":
        print(compare_sqlglot(args.manifest, dialect=args.dialect))
        return 0
    if args.baseline == "dbt_manifest":
        print(compare_dbt_manifest(args.manifest, dialect=args.dialect))
        return 0
    if args.baseline == "canva":
        print(compare_canva())
        return 0
    raise SystemExit(f"Unsupported baseline {args.baseline!r}")


def compare_sqlglot(manifest: Path, *, dialect: str) -> str:
    downstream_index = build_raw_downstream_index(
        manifest_path=manifest,
        dialect=dialect,
    )
    project = load_project(manifest, dialect=dialect)
    artifact = build_catalog_artifact_from_project(project, dialect=dialect)
    downstream_result = trace_downstream_from_artifact(
        artifact,
        selection="raw_payments.amount",
    ).related_ids

    typed_schema = project.typed_schema()
    raw_downstream = downstream_index.get("raw_payments.amount", [])
    formatted_downstream = [item.removeprefix("column:") for item in downstream_result]

    lines = [
        "## sqlglot.lineage comparison",
        "",
        f"- Typed schema tables available to sqlglot: `{len(typed_schema)}`.",
        f"- Raw sqlglot reverse scan returned: `{raw_downstream}`.",
        f"- `clearmetric-core` downstream traversal returned: `{formatted_downstream}`.",
    ]
    return "\n".join(lines)


def compare_dbt_manifest(manifest: Path, *, dialect: str) -> str:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
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
    project = load_project(manifest, dialect=dialect)
    artifact = build_catalog_artifact_from_project(project, dialect=dialect)
    column_downstream = trace_downstream_from_artifact(
        artifact,
        selection="raw_payments.amount",
    ).related_ids
    formatted_columns = [item.removeprefix("column:") for item in column_downstream]

    lines = [
        "## dbt manifest lineage comparison",
        "",
        f"- `child_map` model descendants of `raw_payments`: `{model_names}`.",
        f"- `clearmetric-core` column blast radius for `raw_payments.amount`: `{formatted_columns}`.",
    ]
    return "\n".join(lines)


def compare_canva() -> str:
    try:
        importlib.import_module("dbt_column_lineage_extractor")
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Canva comparison requires `dbt-column-lineage-extractor`."
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
        f"- direct command: `{_first_usage_line(direct_help.stdout)}`",
        f"- recursive command: `{_first_usage_line(recursive_help.stdout)}`",
    ]
    return "\n".join(lines)


def _first_usage_line(help_text: str) -> str:
    for line in help_text.splitlines():
        if line.startswith("usage: "):
            return line
    raise SystemExit("Expected a usage line in CLI help output.")


if __name__ == "__main__":
    raise SystemExit(main())
