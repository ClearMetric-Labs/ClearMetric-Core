"""Resolve paths for example notebooks (local repo or GitHub cache)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType

GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/ClearMetric-Labs/ClearMetric-Core/main"
)
GITHUB_CACHE = Path.home() / ".cache" / "clearmetric" / "github-main"
CACHED_NOTEBOOKS_DIR = GITHUB_CACHE / "examples" / "notebooks"


def _github_raw_base() -> str:
    return os.environ.get("CM_CLEARMETRIC_GITHUB_RAW_BASE", GITHUB_RAW_BASE)


LINEAGE_DEMO_FILES = (
    "examples/lineage-demo/clearmetric.yaml",
    "examples/lineage-demo/warehouse_schema.json",
    "examples/lineage-demo/policy/rules.yaml",
    "examples/lineage-demo/sql/orders_base.sql",
    "examples/lineage-demo/sql/customer_totals.sql",
    "examples/lineage-demo/sql/customers_report.sql",
)

MINIMAL_BUNDLE_FILES = (
    "examples/consumers/bundles/minimal/bundle.manifest.json",
    "examples/consumers/bundles/minimal/catalog.json",
    "examples/consumers/bundles/minimal/graph.json",
    "examples/consumers/bundles/minimal/meta.json",
    "examples/consumers/bundles/minimal/impacts/orders_base.amount_upstream.json",
)

LINEAGE_DEMO_BUNDLE_FILES = (
    "examples/consumers/bundles/lineage-demo/bundle.manifest.json",
    "examples/consumers/bundles/lineage-demo/catalog.json",
    "examples/consumers/bundles/lineage-demo/graph.json",
    "examples/consumers/bundles/lineage-demo/meta.json",
    "examples/consumers/bundles/lineage-demo/impacts/orders_base.amount_downstream.json",
)

MINIMAL_SCENARIO_FILES = (
    "examples/consumers/scenarios/minimal/scenario.yaml",
    "examples/consumers/scenarios/minimal/checks.yaml",
)

LINEAGE_DEMO_SCENARIO_FILES = (
    "examples/consumers/scenarios/lineage-demo/scenario.yaml",
    "examples/consumers/scenarios/lineage-demo/checks.yaml",
)

BACKBONE_LAB_FILES = (
    "examples/backbone-lab/clearmetric.yaml",
    "examples/backbone-lab/policy/rules.yaml",
    "examples/backbone-lab/intent/metrics.yaml",
    "examples/backbone-lab/fixtures/seed.sql",
    "packages/clearmetric-core/tests/fixtures/wedge/jaffle_warehouse_schema.json",
    "packages/clearmetric-core/tests/fixtures/lineage/projects/jaffle_shop/manifest.json",
    "packages/clearmetric-core/tests/fixtures/lineage/projects/jaffle_shop/compiled/customers.sql",
    "packages/clearmetric-core/tests/fixtures/lineage/projects/jaffle_shop/compiled/orders.sql",
    "packages/clearmetric-core/tests/fixtures/lineage/projects/jaffle_shop/compiled/stg_customers.sql",
    "packages/clearmetric-core/tests/fixtures/lineage/projects/jaffle_shop/compiled/stg_orders.sql",
    "packages/clearmetric-core/tests/fixtures/lineage/projects/jaffle_shop/compiled/stg_payments.sql",
)

NOTEBOOK_HELPER_FILES = (
    "examples/notebooks/_notebook_setup.py",
    "examples/notebooks/_paths.py",
)

CHECKS_RUNNER_FILE = ("packages/clearmetric-core/tests/consumers/checks_runner.py",)

ALL_GITHUB_ASSET_FILES = (
    NOTEBOOK_HELPER_FILES
    + LINEAGE_DEMO_FILES
    + MINIMAL_BUNDLE_FILES
    + LINEAGE_DEMO_BUNDLE_FILES
    + MINIMAL_SCENARIO_FILES
    + LINEAGE_DEMO_SCENARIO_FILES
    + BACKBONE_LAB_FILES
    + CHECKS_RUNNER_FILE
)


def _local_repo_root(start: Path | None = None) -> Path | None:
    start = start or Path.cwd()
    for candidate in (start, *start.parents):
        if (candidate / "packages" / "clearmetric-core").is_dir():
            return candidate
    return None


def repo_root(start: Path | None = None) -> Path:
    root = _local_repo_root(start)
    if root is not None:
        return root
    raise FileNotFoundError(
        "ClearMetric-Core repo root not found. "
        "Run bootstrap() in a notebook or clone the repository."
    )


def _fetch_github_file(repo_relative: str, dest: Path) -> None:
    if dest.is_file():
        return
    url = f"{_github_raw_base()}/{repo_relative}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            dest.write_bytes(response.read())
    except urllib.error.URLError as exc:
        raise FileNotFoundError(
            f"Failed to download {url}. Check network access and branch path."
        ) from exc


def sync_github_files(repo_relative_paths: tuple[str, ...]) -> Path:
    """Download repo files into a mirror cache; return cache root."""
    for relative in repo_relative_paths:
        _fetch_github_file(relative, GITHUB_CACHE / relative)
    return GITHUB_CACHE


def seed_github_cache_from_repo(repo_root: Path) -> Path:
    """Populate the GitHub mirror cache from a local checkout (Colab-sim / CI)."""
    for relative in ALL_GITHUB_ASSET_FILES:
        source = repo_root / relative
        if not source.is_file():
            raise FileNotFoundError(f"Missing asset for cache seed: {source}")
        dest = GITHUB_CACHE / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    return GITHUB_CACHE


def lineage_demo_project(start: Path | None = None) -> Path:
    root = _local_repo_root(start)
    local = root / "examples" / "lineage-demo" if root else None
    if local is not None and (local / "clearmetric.yaml").is_file():
        return local
    sync_github_files(LINEAGE_DEMO_FILES)
    return GITHUB_CACHE / "examples/lineage-demo"


def backbone_lab_project(start: Path | None = None) -> Path:
    root = _local_repo_root(start)
    local = root / "examples" / "backbone-lab" if root else None
    if local is not None and (local / "clearmetric.yaml").is_file():
        return local
    sync_github_files(BACKBONE_LAB_FILES)
    return GITHUB_CACHE / "examples/backbone-lab"


_CONSUMER_BUNDLE_SCENARIOS = frozenset({"minimal", "lineage-demo"})


def consumer_bundle_dir(
    scenario_id: str = "minimal", start: Path | None = None
) -> Path:
    if scenario_id not in _CONSUMER_BUNDLE_SCENARIOS:
        raise ValueError(
            f"unknown consumer bundle scenario: {scenario_id!r} "
            f"(expected one of {sorted(_CONSUMER_BUNDLE_SCENARIOS)})"
        )
    root = _local_repo_root(start)
    local = root / "examples" / "consumers" / "bundles" / scenario_id if root else None
    if local is not None and (local / "bundle.manifest.json").is_file():
        return local
    manifest = (
        MINIMAL_BUNDLE_FILES if scenario_id == "minimal" else LINEAGE_DEMO_BUNDLE_FILES
    )
    sync_github_files(manifest)
    return GITHUB_CACHE / "examples/consumers/bundles" / scenario_id


def consumer_scenario(scenario_id: str = "minimal", start: Path | None = None) -> Path:
    if scenario_id not in _CONSUMER_BUNDLE_SCENARIOS:
        raise ValueError(
            f"unknown consumer scenario: {scenario_id!r} "
            f"(expected one of {sorted(_CONSUMER_BUNDLE_SCENARIOS)})"
        )
    root = _local_repo_root(start)
    local = root / "examples/consumers/scenarios" / scenario_id if root else None
    if local is not None and (local / "scenario.yaml").is_file():
        return local
    manifest = (
        MINIMAL_SCENARIO_FILES
        if scenario_id == "minimal"
        else LINEAGE_DEMO_SCENARIO_FILES
    )
    sync_github_files(manifest)
    return GITHUB_CACHE / "examples/consumers/scenarios" / scenario_id


def build_bundle_script(start: Path | None = None) -> Path:
    return repo_root(start) / "scripts" / "consumers" / "build_bundle.py"


def consumer_checks_runner_path(start: Path | None = None) -> Path:
    root = _local_repo_root(start)
    if root is not None:
        path = root / CHECKS_RUNNER_FILE[0]
        if path.is_file():
            return path
    sync_github_files(CHECKS_RUNNER_FILE)
    return GITHUB_CACHE / CHECKS_RUNNER_FILE[0]


def load_checks_runner(start: Path | None = None) -> ModuleType:
    """Load the centralized corpus checks runner (clone or GitHub cache)."""
    path = consumer_checks_runner_path(start)
    spec = importlib.util.spec_from_file_location("checks_runner", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _print_warehouse_schema_summary(path: Path) -> None:
    import json

    from clearmetric.adapters.warehouse import WarehouseMetadataDocument

    payload = json.loads(path.read_text(encoding="utf-8"))
    document = WarehouseMetadataDocument.model_validate(payload)
    column_count = sum(len(table.columns) for table in document.tables)
    print(
        f"warehouse={document.warehouse}  "
        f"tables={len(document.tables)}  columns={column_count}  "
        f"size={path.stat().st_size:,} bytes"
    )
    print("\nTable index:")
    for table in document.tables:
        database = table.database or "—"
        schema = table.schema_name or "—"
        print(f"  {database}.{schema}.{table.name} ({len(table.columns)} cols)")


def show_raw_sources(project_dir: Path) -> None:
    """Print on-disk source files before ClearMetric ingest/merge (display only)."""
    from clearmetric.compiler import discover

    project_dir = Path(project_dir).resolve()
    config_path = project_dir / "clearmetric.yaml"
    print(f"=== config: {config_path.name} ===")
    print(config_path.read_text())

    for src in discover(project_dir).sources:
        if not src.path:
            continue
        path = Path(src.path)
        if src.kind == "sql" and path.is_dir():
            for sql in sorted(path.glob("*.sql")):
                rel = sql.relative_to(project_dir)
                print(f"\n=== {rel} ===")
                print(sql.read_text())
            continue
        if not path.is_file():
            continue
        try:
            label = path.relative_to(project_dir)
        except ValueError:
            label = path.name
        print(f"\n=== {label} ===")
        if src.kind == "warehouse" or path.name == "warehouse_schema.json":
            _print_warehouse_schema_summary(path)
            continue
        print(path.read_text())

    policy_path = project_dir / "policy" / "rules.yaml"
    if policy_path.is_file():
        print(f"\n=== {policy_path.relative_to(project_dir)} ===")
        print(policy_path.read_text())
