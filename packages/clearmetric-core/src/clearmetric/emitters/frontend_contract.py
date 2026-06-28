"""Frontend contract emitter."""

from __future__ import annotations

import json

from clearmetric.compiler.models import CompiledGraph
from clearmetric.core.contracts import parse_query_contract, query_execution_sql
from clearmetric.policy import gate
from clearmetric.policy.models import PolicyRulesFile


def emit_frontend_contract(
    compiled: CompiledGraph,
    *,
    identity: str,
    rules: PolicyRulesFile,
) -> str:
    gated = gate(compiled.artifact, identity=identity, rules=rules)
    contracts: list[dict] = []
    for node in gated.nodes:
        if node.kind != "query":
            continue
        contract = parse_query_contract(node.aspects)
        contracts.append(
            {
                "id": node.id,
                "name": node.name,
                "sql": query_execution_sql(node),
                "parameters": contract.parameters if contract else {},
            }
        )
    return json.dumps({"version": "1", "queries": contracts}, indent=2, sort_keys=False)
