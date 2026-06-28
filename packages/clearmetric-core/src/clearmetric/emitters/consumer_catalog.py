"""Policy-gated consumer catalog emitter."""

from __future__ import annotations

import json

from clearmetric.compiler.models import CompiledGraph
from clearmetric.core import render_json
from clearmetric.policy.models import PolicyRulesFile
from clearmetric.projection import project_consumer_catalog


def emit_consumer_catalog(
    compiled: CompiledGraph,
    *,
    identity: str,
    rules: PolicyRulesFile,
) -> str:
    catalog = project_consumer_catalog(
        compiled.artifact,
        identity=identity,
        rules=rules,
    )
    return json.dumps(render_json(catalog), indent=2, sort_keys=False)
