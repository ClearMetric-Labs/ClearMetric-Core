"""Policy gate for consumer projections."""

from __future__ import annotations

from clearmetric.core.models import CatalogArtifact
from clearmetric.projection import project_for_emit

from .models import PolicyRulesFile


def gate(
    artifact: CatalogArtifact,
    *,
    identity: str,
    rules: PolicyRulesFile,
) -> CatalogArtifact:
    """Apply policy gate before gated emission or optional impact preview."""
    return project_for_emit(artifact, identity=identity, rules=rules)
