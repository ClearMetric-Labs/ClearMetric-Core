"""Resolver completeness and schema-quality status derivation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Mapping

from clearmetric.core.models import Warning

ResolverStatus = Literal[
    "complete",
    "partial",
    "schema_missing",
    "identity_unresolved",
    "parse_failed",
    "unsupported",
]

TypeStatus = Literal["typed", "names_only", "missing"]

OutputDependencyKind = Literal[
    "passthrough", "literal", "aggregate", "window", "unknown"
]

PartialReason = StrEnum(
    "PartialReason",
    [
        "partial_missing_upstream_column",
        "partial_unresolved_output",
        "partial_star_expansion",
        "partial_value_lineage_filter",
        "partial_union",
        "partial_quoted_output",
    ],
)

UnsupportedReason = StrEnum(
    "UnsupportedReason",
    [
        "dependency_cycle",
        "no_compiled_sql",
        "unsupported_sql_construct",
    ],
)


@dataclass(frozen=True)
class LineageResolutionAspect:
    resolver_status: ResolverStatus
    type_status: TypeStatus
    unknown_edges_possible: bool
    blocking_findings: tuple[str, ...]
    schema_names_available: bool
    schema_types_available: bool
    schema_source: str
    output_dependency_kinds: dict[str, OutputDependencyKind] = field(
        default_factory=dict
    )

    def to_aspect_dict(self) -> dict:
        payload = {
            "aspect": "lineage_resolution",
            "resolver_status": self.resolver_status,
            "type_status": self.type_status,
            "unknown_edges_possible": self.unknown_edges_possible,
            "blocking_findings": list(self.blocking_findings),
            "schema_names_available": self.schema_names_available,
            "schema_types_available": self.schema_types_available,
            "schema_source": self.schema_source,
        }
        if self.output_dependency_kinds:
            payload["output_dependency_kinds"] = dict(self.output_dependency_kinds)
        return payload


@dataclass(frozen=True)
class ResolverStatusInput:
    edge_count: int
    declared_column_count: int
    output_column_count: int
    warnings: tuple[Warning, ...]
    type_status: TypeStatus
    schema_names_available: bool
    schema_types_available: bool
    schema_source: str
    parse_failed: bool = False
    dependency_cycle: bool = False
    no_compiled_sql: bool = False
    literal_outputs: frozenset[str] = frozenset()
    output_dependency_kinds: dict[str, OutputDependencyKind] = field(
        default_factory=dict
    )


def derive_type_status(
    *,
    column_names: tuple[str, ...],
    column_types: dict[str, str],
) -> TypeStatus:
    if not column_names:
        return "missing"
    typed_count = sum(1 for name in column_names if name in column_types)
    if typed_count == len(column_names):
        return "typed"
    if typed_count == 0:
        return "names_only" if column_names else "missing"
    return "names_only"


def _warning_codes(warnings: tuple[Warning, ...]) -> set[str]:
    return {warning.code for warning in warnings}


def _blocking_findings_from_warnings(
    warnings: tuple[Warning, ...],
) -> tuple[str, ...]:
    findings: list[str] = []
    for warning in warnings:
        if warning.code in {
            "unresolved_star_source",
            "unresolved_output_source",
            "unresolved_lineage",
            "missing_column_type",
            "select_star",
            "lineage_resolution_failed",
            "dependency_cycle",
            "schema_missing",
            "partial_union",
        }:
            mapped = _map_warning_to_finding(warning.code)
            if mapped not in findings:
                findings.append(mapped)
    return tuple(findings)


def _map_warning_to_finding(code: str) -> str:
    mapping = {
        "unresolved_star_source": PartialReason.partial_star_expansion.value,
        "unresolved_output_source": PartialReason.partial_unresolved_output.value,
        "unresolved_lineage": PartialReason.partial_missing_upstream_column.value,
        "lineage_resolution_failed": "parse_failed",
        "dependency_cycle": UnsupportedReason.dependency_cycle.value,
        "select_star": PartialReason.partial_star_expansion.value,
        "schema_missing": "schema_missing",
        "partial_union": PartialReason.partial_union.value,
    }
    return mapping.get(code, code)


def derive_resolver_status(inputs: ResolverStatusInput) -> LineageResolutionAspect:
    """Derive persisted resolver status from build outputs."""
    warning_codes = _warning_codes(inputs.warnings)
    blocking = _blocking_findings_from_warnings(inputs.warnings)

    if "partial_union" in blocking and inputs.edge_count == 0:
        return LineageResolutionAspect(
            resolver_status="unsupported",
            type_status=inputs.type_status,
            unknown_edges_possible=True,
            blocking_findings=blocking or (PartialReason.partial_union.value,),
            schema_names_available=inputs.schema_names_available,
            schema_types_available=inputs.schema_types_available,
            schema_source=inputs.schema_source,
        )

    if inputs.dependency_cycle:
        return LineageResolutionAspect(
            resolver_status="unsupported",
            type_status=inputs.type_status,
            unknown_edges_possible=True,
            blocking_findings=("dependency_cycle",),
            schema_names_available=inputs.schema_names_available,
            schema_types_available=inputs.schema_types_available,
            schema_source=inputs.schema_source,
            output_dependency_kinds=dict(inputs.output_dependency_kinds),
        )

    if inputs.no_compiled_sql:
        return LineageResolutionAspect(
            resolver_status="unsupported",
            type_status="missing",
            unknown_edges_possible=True,
            blocking_findings=("no_compiled_sql",),
            schema_names_available=False,
            schema_types_available=False,
            schema_source=inputs.schema_source,
        )

    if inputs.parse_failed or "lineage_resolution_failed" in warning_codes:
        return LineageResolutionAspect(
            resolver_status="parse_failed",
            type_status=inputs.type_status,
            unknown_edges_possible=True,
            blocking_findings=blocking or ("parse_failed",),
            schema_names_available=inputs.schema_names_available,
            schema_types_available=inputs.schema_types_available,
            schema_source=inputs.schema_source,
            output_dependency_kinds=dict(inputs.output_dependency_kinds),
        )

    if inputs.type_status == "missing" and inputs.edge_count == 0:
        return LineageResolutionAspect(
            resolver_status="schema_missing",
            type_status="missing",
            unknown_edges_possible=True,
            blocking_findings=blocking or ("schema_missing",),
            schema_names_available=inputs.schema_names_available,
            schema_types_available=inputs.schema_types_available,
            schema_source=inputs.schema_source,
        )

    has_unresolved = bool(
        warning_codes
        & {
            "unresolved_star_source",
            "unresolved_output_source",
            "unresolved_lineage",
        }
    )
    has_identity_issue = (
        any(code.endswith("_failed") for code in warning_codes)
        or "identity_unresolved" in blocking
    )

    if has_identity_issue and inputs.edge_count == 0:
        return LineageResolutionAspect(
            resolver_status="identity_unresolved",
            type_status=inputs.type_status,
            unknown_edges_possible=True,
            blocking_findings=blocking or ("identity_unresolved",),
            schema_names_available=inputs.schema_names_available,
            schema_types_available=inputs.schema_types_available,
            schema_source=inputs.schema_source,
        )

    if inputs.edge_count == 0 and inputs.type_status == "missing":
        return LineageResolutionAspect(
            resolver_status="schema_missing",
            type_status="missing",
            unknown_edges_possible=True,
            blocking_findings=blocking or ("schema_missing",),
            schema_names_available=inputs.schema_names_available,
            schema_types_available=inputs.schema_types_available,
            schema_source=inputs.schema_source,
        )

    if has_unresolved or blocking:
        non_literal_outputs = max(
            0,
            inputs.output_column_count - len(inputs.literal_outputs),
        )
        covered = inputs.edge_count
        if non_literal_outputs > 0 and covered < non_literal_outputs:
            return LineageResolutionAspect(
                resolver_status="partial",
                type_status=inputs.type_status,
                unknown_edges_possible=True,
                blocking_findings=blocking,
                schema_names_available=inputs.schema_names_available,
                schema_types_available=inputs.schema_types_available,
                schema_source=inputs.schema_source,
                output_dependency_kinds=dict(inputs.output_dependency_kinds),
            )

    if inputs.edge_count > 0 and not has_unresolved:
        return LineageResolutionAspect(
            resolver_status="complete",
            type_status=inputs.type_status,
            unknown_edges_possible=False,
            blocking_findings=(),
            schema_names_available=inputs.schema_names_available,
            schema_types_available=inputs.schema_types_available,
            schema_source=inputs.schema_source,
            output_dependency_kinds=dict(inputs.output_dependency_kinds),
        )

    if inputs.edge_count == 0:
        return LineageResolutionAspect(
            resolver_status="schema_missing"
            if inputs.type_status == "missing"
            else "partial",
            type_status=inputs.type_status,
            unknown_edges_possible=True,
            blocking_findings=blocking or ("no_edges_emitted",),
            schema_names_available=inputs.schema_names_available,
            schema_types_available=inputs.schema_types_available,
            schema_source=inputs.schema_source,
        )

    return LineageResolutionAspect(
        resolver_status="partial",
        type_status=inputs.type_status,
        unknown_edges_possible=True,
        blocking_findings=blocking,
        schema_names_available=inputs.schema_names_available,
        schema_types_available=inputs.schema_types_available,
        schema_source=inputs.schema_source,
        output_dependency_kinds=dict(inputs.output_dependency_kinds),
    )


def resolver_coverage_summary(per_model: Mapping[str, object]) -> dict[str, int]:
    """Aggregate resolver_status counts from production per-model build results."""
    status_keys: tuple[str, ...] = (
        "complete",
        "partial",
        "schema_missing",
        "identity_unresolved",
        "parse_failed",
        "unsupported",
        "unknown",
    )
    counts: dict[str, int] = dict.fromkeys(status_keys, 0)
    unknown_edges_possible = 0
    for result in per_model.values():
        status = getattr(result, "resolver_status", None)
        if status is None:
            resolution = getattr(result, "lineage_resolution", None) or {}
            if isinstance(resolution, dict):
                status = resolution.get("resolver_status")
        key = str(status) if status else "unknown"
        if key not in counts:
            counts[key] = 0
        counts[key] += 1
        resolution = getattr(result, "lineage_resolution", None) or {}
        if isinstance(resolution, dict) and resolution.get("unknown_edges_possible"):
            unknown_edges_possible += 1
    counts["unknown_edges_possible"] = unknown_edges_possible
    return counts
