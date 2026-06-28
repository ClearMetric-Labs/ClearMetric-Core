"""Structural graph checks."""

from __future__ import annotations

from clearmetric.core.interop import physical_binding_key
from clearmetric.graph import GraphView

from .models import Finding


def _finding(
    *,
    check_id: str,
    tier: str,
    message: str,
    node_id: str | None = None,
    fix_hint: str | None = None,
) -> Finding:
    return Finding(
        check_id=check_id,
        node_id=node_id,
        severity=None,
        message=message,
        fix_hint=fix_hint,
        tier=tier,
    )


def check_unique_node_ids(view: GraphView) -> list[Finding]:
    seen: set[str] = set()
    findings: list[Finding] = []
    for node in view.nodes():
        if node.id in seen:
            findings.append(
                _finding(
                    check_id="check.unique_node_ids",
                    node_id=node.id,
                    tier="structural",
                    message=f"Duplicate node id {node.id!r}",
                    fix_hint="Ensure each logical node has a unique canonical id",
                )
            )
        seen.add(node.id)
    return findings


def check_edges_resolve(view: GraphView) -> list[Finding]:
    node_ids = {node.id for node in view.nodes()}
    findings: list[Finding] = []
    for edge in view.edges():
        if edge.source_id not in node_ids:
            findings.append(
                _finding(
                    check_id="check.edges_resolve",
                    node_id=edge.source_id,
                    tier="structural",
                    message=(
                        f"Edge {edge.kind} references missing source node "
                        f"{edge.source_id!r}"
                    ),
                    fix_hint="Remove or repair dangling edge endpoints",
                )
            )
        if edge.target_id not in node_ids:
            findings.append(
                _finding(
                    check_id="check.edges_resolve",
                    node_id=edge.target_id,
                    tier="structural",
                    message=(
                        f"Edge {edge.kind} references missing target node "
                        f"{edge.target_id!r}"
                    ),
                    fix_hint="Remove or repair dangling edge endpoints",
                )
            )
    return findings


def check_duplicate_bindings(view: GraphView) -> list[Finding]:
    binding_to_node: dict[tuple[str, str, str, str, str], str] = {}
    findings: list[Finding] = []
    for node in view.nodes():
        if not node.bindings:
            continue
        for binding in node.bindings:
            key = physical_binding_key(binding)
            if not any(key):
                continue
            existing = binding_to_node.get(key)
            if existing is not None and existing != node.id:
                findings.append(
                    _finding(
                        check_id="check.duplicate_bindings",
                        node_id=node.id,
                        tier="structural",
                        message=(
                            f"Physical binding {key!r} is shared by {existing!r} "
                            f"and {node.id!r}"
                        ),
                        fix_hint="Ensure each logical node has distinct physical bindings",
                    )
                )
            else:
                binding_to_node[key] = node.id
    return findings


def check_partial_derivation(view: GraphView) -> list[Finding]:
    findings: list[Finding] = []
    for node in view.nodes():
        if node.derivation is None:
            continue
        if node.derivation.status == "partial":
            findings.append(
                _finding(
                    check_id="check.partial_derivation",
                    node_id=node.id,
                    tier="warn",
                    message=(
                        f"{node.id} has derivation status {node.derivation.status!r} "
                        f"(confidence={node.derivation.confidence!r})"
                    ),
                    fix_hint="Review lineage resolution for this node",
                )
            )
        elif node.derivation.status == "failed":
            findings.append(
                _finding(
                    check_id="check.failed_derivation",
                    node_id=node.id,
                    tier="error",
                    message=(
                        f"{node.id} has derivation status {node.derivation.status!r} "
                        f"(confidence={node.derivation.confidence!r})"
                    ),
                    fix_hint="Review lineage resolution for this node",
                )
            )
    return findings
