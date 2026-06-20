"""Text renderer for catalogkit-query."""

from __future__ import annotations

from ..models import QueryMap


def render_text(query_map: QueryMap) -> str:
    """Render the public catalogkit-query artifact for human reading."""
    lines: list[str] = []
    summary = query_map.summary

    lines.append("catalogkit-query")
    lines.append(f"dialect: {summary.dialect}")
    lines.append(f"statement_type: {summary.statement_type}")
    lines.append(f"has_ctes: {summary.has_ctes}")
    lines.append("")

    lines.append("relations:")
    for relation in query_map.relations:
        display_name = relation.qualified_name or relation.name
        lines.append(f"  - [{relation.kind}] {display_name}")

    lines.append("")
    lines.append("relation_usages:")
    for usage in query_map.relation_usages:
        alias = f" alias={usage.alias}" if usage.alias else ""
        lines.append(f"  - {usage.context}: {usage.relation_id}{alias} :: {usage.sql}")

    lines.append("")
    lines.append("dependencies:")
    for edge in query_map.edges:
        lines.append(f"  - {edge.source_id} -> {edge.target_id}")

    if query_map.warnings:
        lines.append("")
        lines.append("warnings:")
        for warning in query_map.warnings:
            location = f" [{warning.location}]" if warning.location else ""
            lines.append(f"  - {warning.code}: {warning.message}{location}")

    return "\n".join(lines)
