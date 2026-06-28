"""Compile SQL contracts onto query nodes."""

from __future__ import annotations

from clearmetric.core.contracts import parse_query_contract
from clearmetric.core.errors import ClearMetricError, CompilerError
from clearmetric.core.models import CatalogArtifact, Node
from clearmetric.query.parser import parse_statement


def compile_query_contracts(
    artifact: CatalogArtifact,
    *,
    dialect: str,
) -> CatalogArtifact:
    """Parse and validate query SQL; attach compiled_sql to query aspects."""
    updated_nodes: list[Node] = []
    violations: list[str] = []

    for node in artifact.nodes:
        if node.kind != "query":
            updated_nodes.append(node)
            continue

        contract = parse_query_contract(node.aspects)
        if contract is None:
            updated_nodes.append(node)
            continue

        try:
            parsed = parse_statement(contract.sql, dialect=dialect)
            compiled_sql = parsed.root_expression.sql(dialect=dialect)
        except ClearMetricError as exc:
            violations.append(f"{node.id} query SQL compile failure: {exc}")
            updated_nodes.append(node)
            continue

        aspects = dict(node.aspects or {})
        query_aspect = dict(aspects.get("query") or {})
        query_aspect["compiled_sql"] = compiled_sql
        aspects["query"] = query_aspect
        updated_nodes.append(node.model_copy(update={"aspects": aspects}))

    if violations:
        raise CompilerError("; ".join(violations))

    return CatalogArtifact(
        version=artifact.version,
        nodes=updated_nodes,
        edges=artifact.edges,
        warnings=artifact.warnings,
    )
