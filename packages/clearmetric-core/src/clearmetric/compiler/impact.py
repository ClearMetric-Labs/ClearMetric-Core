"""Impact orchestration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from clearmetric.graph import TraversalDirection
from clearmetric.lineage import (
    trace_downstream_from_artifact,
    trace_upstream_from_artifact,
)
from clearmetric.lineage.models import TraversalResult
from clearmetric.policy import gate, load_rules

from .compile import compile
from .models import CompiledGraph


def impact(
    project_dir: Path,
    *,
    selection: str,
    direction: TraversalDirection,
    identity: str | None = None,
) -> tuple[CompiledGraph, TraversalResult]:
    # compile (not build_graph): impact requires an enforced-valid graph for traversal.
    compiled = compile(project_dir)
    artifact = compiled.artifact
    if identity is not None:
        rules = load_rules(compiled.project.policy.rules)
        artifact = gate(artifact, identity=identity, rules=rules)
        compiled = replace(compiled, artifact=artifact)

    if direction == "upstream":
        result = trace_upstream_from_artifact(artifact, selection=selection)
    else:
        result = trace_downstream_from_artifact(artifact, selection=selection)
    return compiled, result
