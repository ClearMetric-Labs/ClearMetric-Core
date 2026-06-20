"""Serialization helpers for CatalogKit artifacts."""

from __future__ import annotations

from .models import CatalogArtifact


def render_json(artifact: CatalogArtifact) -> dict:
    """Return the canonical JSON-serializable artifact."""
    return artifact.model_dump(mode="json", by_alias=True)
