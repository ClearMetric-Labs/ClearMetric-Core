"""JSON Schema validation for ClearMetric Core."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .errors import ValidationError
from .models import CatalogArtifact

_REPO_CANDIDATES = Path(__file__).resolve().parents


def _spec_root() -> Path:
    for parent in _REPO_CANDIDATES:
        candidate = parent / "spec"
        if (candidate / "clearmetric-project.schema.json").is_file():
            return candidate
    raise ValidationError("Could not locate spec/ directory")


@lru_cache(maxsize=8)
def _load_schema(name: str) -> dict[str, Any]:
    path = _spec_root() / name
    if not path.is_file():
        raise ValidationError(f"Schema file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(data: dict[str, Any], schema_name: str) -> dict[str, Any]:
    schema = _load_schema(schema_name)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda err: list(err.path))
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.path) or "<root>"
        raise ValidationError(
            f"{schema_name} validation failed at {path}: {first.message}"
        )
    return data


def validate_project_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a project config dict against the project schema."""
    return _validate(data, "clearmetric-project.schema.json")


def validate_artifact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Validate an artifact dict against the artifact schema."""
    return _validate(data, "catalog-artifact.schema.json")


def collect_schema_errors(data: dict[str, Any], schema_name: str) -> list[str]:
    """Collect all schema validation errors (batch loud failure at adapter boundary)."""
    schema = _load_schema(schema_name)
    validator = Draft202012Validator(schema)
    messages: list[str] = []
    for error in sorted(validator.iter_errors(data), key=lambda err: list(err.path)):
        path = ".".join(str(part) for part in error.path) or "<root>"
        messages.append(f"{path}: {error.message}")
    return messages


def load_artifact_file(path: Path) -> CatalogArtifact:
    """Load and validate a catalog artifact JSON file."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Artifact file is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValidationError(f"Artifact file must contain a JSON object: {path}")
    validate_artifact_dict(payload)
    return CatalogArtifact.model_validate(payload)
