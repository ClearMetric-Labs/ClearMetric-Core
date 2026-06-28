"""Compiler validation."""

from __future__ import annotations

from clearmetric.cleaner import enforce_checks, run_compile_checks
from clearmetric.cleaner.models import CleanerReport
from clearmetric.core.models import CatalogArtifact
from clearmetric.core.project import Posture
from clearmetric.policy import validate_security_floor

from .contracts import validate_contract_nodes


def check_graph(artifact: CatalogArtifact, *, posture: Posture) -> CleanerReport:
    """Report-only validation; does not raise."""
    return run_compile_checks(artifact, posture=posture)


def enforce_graph(artifact: CatalogArtifact, *, posture: Posture) -> CleanerReport:
    """Enforce structural checks, contract validation, and security floor."""
    validate_contract_nodes(artifact)
    report = enforce_checks(artifact, posture=posture)
    validate_security_floor(artifact)
    return report
