"""Impact orchestration."""

from __future__ import annotations

from pathlib import Path

from clearmetric.core import TraversalResult, table_id
from clearmetric.core.models import CatalogArtifact, Warning
from clearmetric.core.project import load_project_config
from clearmetric.core.validate import load_artifact_file
from clearmetric.graph import (
    TraversalDirection,
    trace_downstream_from_artifact,
    trace_upstream_from_artifact,
    view_of,
)
from clearmetric.policy import (
    filter_allow_only_ids,
    load_rules,
    require_allow,
    require_gated_identity,
)

from .compile import compile
from .models import CompiledGraph


def _lineage_resolution_from_table(
    artifact: CatalogArtifact,
    dataset_name: str,
) -> dict | None:
    node = view_of(artifact).node(table_id(dataset_name))
    aspects = node.aspects or {}
    resolution = aspects.get("lineage_resolution")
    return resolution if isinstance(resolution, dict) else None


def _aggregate_resolution_for_column(
    artifact: CatalogArtifact,
    column_node_id: str,
) -> dict | None:
    """Read resolver completeness from the owning table node's lineage_resolution aspect."""
    node = view_of(artifact).node(column_node_id)
    schema_name = node.model_dump().get("schema")
    if isinstance(schema_name, str) and schema_name:
        return _lineage_resolution_from_table(artifact, schema_name)
    qualified = node.qualified_name or node.name
    if "." in qualified:
        dataset_name = qualified.rsplit(".", 1)[0]
        return _lineage_resolution_from_table(artifact, dataset_name)
    return None


def resolver_completeness_envelope(
    artifact: CatalogArtifact,
    related_ids: tuple[str, ...],
) -> dict:
    """Aggregate resolver status across traversed column endpoints."""
    statuses: list[str] = []
    blocking: set[str] = set()
    unknown_possible = False
    for node_id in related_ids:
        if not node_id.startswith("column:"):
            continue
        resolution = _aggregate_resolution_for_column(artifact, node_id)
        if resolution is None:
            unknown_possible = True
            blocking.add("missing_lineage_resolution_aspect")
            continue
        status = str(resolution.get("resolver_status") or "")
        if status:
            statuses.append(status)
        if resolution.get("unknown_edges_possible"):
            unknown_possible = True
        for finding in resolution.get("blocking_findings") or []:
            blocking.add(str(finding))

    if not statuses:
        completeness = "unknown"
    elif any(
        status in {"partial", "schema_missing", "identity_unresolved", "parse_failed"}
        for status in statuses
    ):
        completeness = "partial"
    elif all(status == "complete" for status in statuses):
        completeness = "complete"
    else:
        completeness = "partial"

    return {
        "resolver_completeness": completeness,
        "unknown_edges_possible": unknown_possible,
        "blocking_findings": sorted(blocking),
        "resolver_statuses": sorted(set(statuses)),
    }


def _filter_traversal_by_identity(
    result: TraversalResult,
    artifact: CatalogArtifact,
    *,
    identity: str,
    rules_path: str | Path,
) -> TraversalResult:
    rules = load_rules(rules_path)
    view = view_of(artifact)
    selection_node = view.node(result.selection_id)
    require_allow(node=selection_node, identity=identity, rules=rules)
    filtered_ids = filter_allow_only_ids(
        node_ids=result.related_ids,
        resolve_node=view.node,
        identity=identity,
        rules=rules,
    )
    return result.model_copy(update={"related_ids": filtered_ids})


def _finalize_impact_result(
    artifact: CatalogArtifact,
    result: TraversalResult,
) -> TraversalResult:
    envelope = resolver_completeness_envelope(artifact, tuple(result.related_ids))
    if (
        envelope["resolver_completeness"] == "partial"
        and not envelope["blocking_findings"]
    ):
        envelope["blocking_findings"] = ["partial_lineage_without_blockers"]

    warnings: list[Warning] = []
    if envelope["resolver_completeness"] != "complete":
        warnings.append(
            Warning(
                code="partial_lineage_impact",
                message=(
                    "Impact traversal crosses models with incomplete lineage resolution: "
                    f"{envelope['resolver_statuses']}"
                ),
                subject_id=result.selection_id,
            )
        )

    return result.model_copy(
        update={
            "resolver_completeness": envelope["resolver_completeness"],
            "unknown_edges_possible": envelope["unknown_edges_possible"],
            "blocking_findings": envelope["blocking_findings"],
            "warnings": [*result.warnings, *warnings],
        }
    )


def impact_from_artifact(
    artifact_path: Path,
    project_dir: Path,
    *,
    selection: str,
    direction: TraversalDirection,
    identity: str | None = None,
) -> tuple[CompiledGraph, TraversalResult]:
    """Trace lineage impact using a persisted CatalogArtifact (no compile)."""
    root = project_dir.expanduser().resolve()
    project = load_project_config(root)
    artifact = load_artifact_file(artifact_path.expanduser().resolve())

    if direction == "upstream":
        result = trace_upstream_from_artifact(artifact, selection=selection)
    else:
        result = trace_downstream_from_artifact(artifact, selection=selection)

    if identity is not None:
        identity = require_gated_identity(identity)
        result = _filter_traversal_by_identity(
            result,
            artifact,
            identity=identity,
            rules_path=project.policy.rules,
        )

    result = _finalize_impact_result(artifact, result)
    compiled = CompiledGraph(
        artifact=artifact,
        project=project,
        project_dir=root,
        sources_run=[],
    )
    return compiled, result


def impact(
    project_dir: Path,
    *,
    selection: str,
    direction: TraversalDirection,
    identity: str | None = None,
) -> tuple[CompiledGraph, TraversalResult]:
    """Trace lineage impact on the full enforced graph."""
    compiled = compile(project_dir)
    artifact = compiled.artifact

    if direction == "upstream":
        result = trace_upstream_from_artifact(artifact, selection=selection)
    else:
        result = trace_downstream_from_artifact(artifact, selection=selection)

    if identity is not None:
        identity = require_gated_identity(identity)
        result = _filter_traversal_by_identity(
            result,
            artifact,
            identity=identity,
            rules_path=compiled.project.policy.rules,
        )

    result = _finalize_impact_result(artifact, result)
    return compiled, result
