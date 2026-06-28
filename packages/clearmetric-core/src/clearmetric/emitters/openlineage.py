"""OpenLineage emitter."""

from __future__ import annotations

import json

from clearmetric.compiler.models import CompiledGraph
from clearmetric.lineage import build_openlineage_export
from clearmetric.policy import gate
from clearmetric.policy.models import PolicyRulesFile


def emit_openlineage(
    compiled: CompiledGraph,
    *,
    identity: str,
    rules: PolicyRulesFile,
) -> str:
    gated = gate(compiled.artifact, identity=identity, rules=rules)
    payload = build_openlineage_export(
        gated,
        job_name=compiled.project_dir.name,
    )
    return json.dumps(payload, indent=2, sort_keys=False)
