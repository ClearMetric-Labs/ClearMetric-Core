"""Shared sqlglot AST helpers for catalogkit-query."""

from __future__ import annotations

from collections.abc import Iterable

from sqlglot import exp


def iter_ctes(root_expression: exp.Expression) -> Iterable[exp.CTE]:
    """Yield every CTE in the parsed query."""
    yield from root_expression.find_all(exp.CTE)


def cte_name(cte: exp.CTE) -> str:
    """Return the raw CTE name from the AST."""
    alias = cte.alias
    if isinstance(alias, str):
        return alias
    return str(alias or "").strip()


def qualified_table_name(table: exp.Table) -> str:
    """Return the dotted table name as it appears in the AST."""
    parts = [
        str(part).strip()
        for part in (table.catalog, table.db, table.name)
        if str(part or "").strip()
    ]
    return ".".join(parts)


def has_join_ancestor(node: exp.Expression, *, stop_node: exp.Expression) -> bool:
    """Detect whether a table node appears under a JOIN subtree."""
    current = node.parent
    while current is not None and current is not stop_node:
        if isinstance(current, exp.Join):
            return True
        current = current.parent
    return False


def iter_table_nodes(expression: exp.Expression | None) -> Iterable[exp.Table]:
    """Yield all table nodes under an expression."""
    if expression is None:
        return
    for table in expression.find_all(exp.Table):
        yield table


def iter_table_nodes_skipping_ctes(
    expression: exp.Expression | None,
) -> Iterable[exp.Table]:
    """Yield table nodes while skipping traversal into nested CTE definitions."""
    if expression is None:
        return
    yield from _walk_tables(expression, skip_ctes=True)


def _walk_tables(expression: exp.Expression, *, skip_ctes: bool) -> Iterable[exp.Table]:
    if skip_ctes and isinstance(expression, exp.CTE):
        return
    if isinstance(expression, exp.Table):
        yield expression
        return
    for child in expression.args.values():
        if isinstance(child, list):
            for item in child:
                if isinstance(item, exp.Expression):
                    yield from _walk_tables(item, skip_ctes=skip_ctes)
        elif isinstance(child, exp.Expression):
            yield from _walk_tables(child, skip_ctes=skip_ctes)
