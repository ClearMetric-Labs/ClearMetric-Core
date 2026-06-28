"""Impact orchestration."""

from __future__ import annotations

from pathlib import Path

from clearmetric.core import TraversalResult
from clearmetric.graph import (
    TraversalDirection,
    trace_downstream_from_artifact,
    trace_upstream_from_artifact,
)

from .compile import compile
from .models import CompiledGraph


def impact(
    project_dir: Path,
    *,
    selection: str,
    direction: TraversalDirection,
) -> tuple[CompiledGraph, TraversalResult]:
    """Trace lineage impact on the full enforced graph."""
    compiled = compile(project_dir)
    artifact = compiled.artifact

    if direction == "upstream":
        result = trace_upstream_from_artifact(artifact, selection=selection)
    else:
        result = trace_downstream_from_artifact(artifact, selection=selection)
    return compiled, result
