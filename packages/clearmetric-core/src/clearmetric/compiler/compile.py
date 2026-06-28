"""Compile orchestration."""

from __future__ import annotations

from pathlib import Path

from .models import CompiledGraph
from .pipeline import run_build
from .validate import enforce_graph


def build_graph(project_dir: Path) -> CompiledGraph:
    """Ingest, merge, and bind warehouse metadata without enforcing checks."""
    return run_build(project_dir)


def compile(project_dir: Path) -> CompiledGraph:
    """Build and enforce a valid compiled graph."""
    compiled = build_graph(project_dir)
    enforce_graph(compiled.artifact, posture=compiled.project.posture)
    return compiled
