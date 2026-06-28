"""Posture-aware check severity and registry."""

from __future__ import annotations

from clearmetric.core.errors import StructuralCheckError
from clearmetric.core.models import Warning
from clearmetric.core.project import Posture
from clearmetric.graph import view_of

from .models import CleanerReport, Finding, Severity
from .specs import CHECKS

CheckTier = str  # structural | error | warn

WARNING_CODE_TIERS: dict[str, CheckTier] = {
    "schema_drift": "warn",
    "source_disagreement": "warn",
    "warehouse_bind_unresolved": "warn",
    "warehouse_bind_ambiguous": "warn",
}


def resolve_severity(tier: CheckTier, posture: Posture) -> Severity | None:
    """Map check tier + posture to finding severity. None means off."""
    if tier == "structural":
        return "error"
    if posture == "strict":
        if tier == "error":
            return "error"
        if tier == "warn":
            return "warn"
        return None
    if posture == "standard":
        if tier in {"error", "warn"}:
            return "warn"
        return None
    if posture == "permissive":
        return None
    raise ValueError(f"Unknown posture: {posture!r}")


def warnings_to_findings(warnings: list[Warning], posture: Posture) -> list[Finding]:
    findings: list[Finding] = []
    for warning in warnings:
        tier = WARNING_CODE_TIERS.get(warning.code, "warn")
        severity = resolve_severity(tier, posture)
        if severity is None:
            continue
        findings.append(
            Finding(
                check_id=f"check.{warning.code}",
                node_id=warning.subject_id,
                severity=severity,
                message=warning.message,
                tier=tier,
            )
        )
    return findings


def run_compile_checks(artifact, *, posture: Posture) -> CleanerReport:
    view = view_of(artifact)
    findings: list[Finding] = []
    for check_fn in CHECKS:
        for finding in check_fn(view):
            tier = finding.tier
            if tier is None:
                raise ValueError(
                    f"{finding.check_id} must set finding.tier for posture resolution"
                )
            severity = resolve_severity(tier, posture)
            if severity is None:
                continue
            findings.append(
                finding.model_copy(update={"severity": severity, "tier": tier})
            )
    findings.extend(warnings_to_findings(artifact.warnings, posture))
    return CleanerReport(findings=findings)


def enforce_checks(artifact, *, posture: Posture) -> CleanerReport:
    report = run_compile_checks(artifact, posture=posture)
    errors = [finding for finding in report.findings if finding.severity == "error"]
    if errors:
        messages = "; ".join(
            f"{finding.check_id}: {finding.message}" for finding in errors
        )
        raise StructuralCheckError(messages)
    return report
