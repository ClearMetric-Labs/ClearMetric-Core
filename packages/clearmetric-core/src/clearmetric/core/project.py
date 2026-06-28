"""Project configuration loading for ClearMetric Core."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from clearmetric.policy.load import load_rules
from pydantic import BaseModel, Field, ValidationError

from .aliases import load_table_alias_map
from .errors import PolicyError, ProjectConfigError
from .errors import ValidationError as ArtifactValidationError
from .interop import AliasMap
from .validate import validate_project_dict

Posture = Literal["strict", "standard", "permissive"]
WarehouseKind = Literal["information_schema"]

_RUNTIME_WAREHOUSE_KEYS = frozenset(
    {"execute", "query", "runtime", "connection_string"}
)


class WarehouseSource(BaseModel):
    kind: WarehouseKind
    path: str
    database: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")


class DbtSource(BaseModel):
    manifest: str | None = None


class SqlSource(BaseModel):
    paths: list[str] = Field(default_factory=list)


class IntentSource(BaseModel):
    paths: list[str] = Field(default_factory=list)


class ProjectSources(BaseModel):
    warehouse: WarehouseSource | None = None
    dbt: DbtSource | None = None
    sql: SqlSource | None = None
    intent: IntentSource | None = None


class PolicyConfig(BaseModel):
    rules: str


class ClearMetricProject(BaseModel):
    version: Literal[1]
    dialect: str
    sources: ProjectSources
    posture: Posture
    policy: PolicyConfig
    aliases: str | None = None


def load_project_config(project_dir: Path) -> ClearMetricProject:
    """Load and validate clearmetric.yaml from a project directory."""
    root = project_dir.expanduser().resolve()
    config_path = root / "clearmetric.yaml"
    if not config_path.is_file():
        raise ProjectConfigError(f"Project config not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProjectConfigError(
            f"Project config is not valid YAML: {config_path}"
        ) from exc

    if not isinstance(raw, dict):
        raise ProjectConfigError(
            f"Project config must be a YAML mapping: {config_path}"
        )

    _reject_runtime_warehouse_keys(raw)

    try:
        validate_project_dict(raw)
        project = ClearMetricProject.model_validate(raw)
    except (ArtifactValidationError, ValidationError) as exc:
        raise ProjectConfigError(
            f"Project config failed validation: {config_path}: {exc}"
        ) from exc

    _resolve_project_paths(root, project)
    return project


def _reject_runtime_warehouse_keys(raw: dict) -> None:
    warehouse = (raw.get("sources") or {}).get("warehouse")
    if not isinstance(warehouse, dict):
        return
    runtime_keys = sorted(key for key in warehouse if key in _RUNTIME_WAREHOUSE_KEYS)
    if runtime_keys:
        raise ProjectConfigError(
            "Warehouse runtime/query execution config is not supported in v1: "
            + ", ".join(runtime_keys)
        )


def _resolve_project_paths(root: Path, project: ClearMetricProject) -> None:
    sources = project.sources
    has_source = False

    if sources.warehouse is not None:
        has_source = True
        resolved = _resolve_path(root, sources.warehouse.path)
        sources.warehouse.path = str(resolved)

    if sources.dbt is not None and sources.dbt.manifest:
        has_source = True
        resolved = _resolve_path(root, sources.dbt.manifest)
        sources.dbt.manifest = str(resolved)

    if sources.sql is not None and sources.sql.paths:
        has_source = True
        resolved_paths: list[str] = []
        for path in sources.sql.paths:
            resolved_paths.append(str(_resolve_path(root, path)))
        sources.sql.paths = resolved_paths

    if sources.intent is not None and sources.intent.paths:
        has_source = True
        resolved_paths = []
        for path in sources.intent.paths:
            resolved_paths.append(str(_resolve_path(root, path)))
        sources.intent.paths = resolved_paths

    if not has_source:
        raise ProjectConfigError(
            "Project must configure at least one source: warehouse, dbt.manifest, sql.paths, or intent.paths"
        )

    rules_path = _resolve_path(root, project.policy.rules)
    if not rules_path.is_file():
        raise ProjectConfigError(f"Policy rules file not found: {rules_path}")
    project.policy.rules = str(rules_path)
    try:
        load_rules(rules_path)
    except PolicyError as exc:
        raise ProjectConfigError(
            f"Policy rules failed validation: {rules_path}: {exc}"
        ) from exc

    if project.aliases is not None:
        resolved_aliases = _resolve_path(root, project.aliases)
        if not resolved_aliases.is_file():
            raise ProjectConfigError(f"Aliases file not found: {resolved_aliases}")
        project.aliases = str(resolved_aliases)


def load_project_aliases(project: ClearMetricProject) -> AliasMap | None:
    """Load optional project alias map."""
    if project.aliases is None:
        return None
    return load_table_alias_map(project.aliases)


def _resolve_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.exists():
        raise ProjectConfigError(f"Configured path does not exist: {candidate}")
    return candidate
