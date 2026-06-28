"""Projection filtering."""

from __future__ import annotations

from clearmetric.core.models import CatalogArtifact, Edge, Node
from clearmetric.policy import evaluate_node
from clearmetric.policy.models import PolicyRulesFile

CATALOG_ASSET_KINDS = frozenset({"table", "column", "model"})
CONSUMER_CATALOG_KINDS = frozenset({"table", "column", "model", "metric", "query"})

_MASKED_ASPECT_KEYS = frozenset(
    {"classification", "policy_refs", "ai_behavior", "pii", "confidential"}
)


def _slice_by_kinds(
    artifact: CatalogArtifact,
    *,
    kinds: frozenset[str],
    clear_warnings: bool,
) -> CatalogArtifact:
    nodes = [node for node in artifact.nodes if node.kind in kinds]
    allowed_ids = {node.id for node in nodes}
    edges = [
        edge
        for edge in artifact.edges
        if edge.source_id in allowed_ids and edge.target_id in allowed_ids
    ]
    return CatalogArtifact(
        version=artifact.version,
        nodes=nodes,
        edges=edges,
        warnings=[] if clear_warnings else artifact.warnings,
    )


def project_catalog_assets(artifact: CatalogArtifact) -> CatalogArtifact:
    """Admin unfiltered asset slice for catalog output (no policy filter)."""
    return _slice_by_kinds(artifact, kinds=CATALOG_ASSET_KINDS, clear_warnings=True)


def _apply_mask(node: Node) -> Node:
    aspects = dict(node.aspects or {})
    for key in _MASKED_ASPECT_KEYS:
        aspects.pop(key, None)
    aspects["_policy_masked"] = True
    return node.model_copy(update={"aspects": aspects})


def project_for_emit(
    artifact: CatalogArtifact,
    *,
    identity: str,
    rules: PolicyRulesFile,
) -> CatalogArtifact:
    """Policy-gated projection for consumer emit formats."""
    nodes: list[Node] = []
    allowed_ids: set[str] = set()

    for node in artifact.nodes:
        decision = evaluate_node(node=node, identity=identity, rules=rules)
        if decision == "deny":
            continue
        if decision == "mask":
            nodes.append(_apply_mask(node))
        else:
            nodes.append(node)
        allowed_ids.add(node.id)

    edges: list[Edge] = [
        edge
        for edge in artifact.edges
        if edge.source_id in allowed_ids and edge.target_id in allowed_ids
    ]
    return CatalogArtifact(
        version=artifact.version,
        nodes=nodes,
        edges=edges,
        warnings=artifact.warnings,
    )


def project_consumer_catalog(
    artifact: CatalogArtifact,
    *,
    identity: str,
    rules: PolicyRulesFile,
) -> CatalogArtifact:
    """Policy-gated consumer catalog slice."""
    gated = project_for_emit(artifact, identity=identity, rules=rules)
    return _slice_by_kinds(gated, kinds=CONSUMER_CATALOG_KINDS, clear_warnings=True)
