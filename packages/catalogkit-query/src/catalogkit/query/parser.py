"""sqlglot parsing helpers for catalogkit-query."""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from .errors import QueryMapContractError, QueryMapParseError


@dataclass(frozen=True)
class ParsedStatement:
    statement: exp.Expression
    root_expression: exp.Expression
    dialect: str


def parse_statement(sql: str, *, dialect: str) -> ParsedStatement:
    """Parse exactly one SQL statement into a supported AST."""
    cleaned = (sql or "").strip()
    if not cleaned:
        raise QueryMapParseError("SQL input is empty.")

    try:
        statements = [stmt for stmt in sqlglot.parse(cleaned, read=dialect) if stmt is not None]
    except Exception as exc:
        raise QueryMapParseError(f"Failed to parse SQL with dialect {dialect!r}: {exc}") from exc

    if not statements:
        raise QueryMapParseError("SQL input produced no parseable statements.")
    if len(statements) != 1:
        raise QueryMapContractError("catalogkit-query accepts exactly one SQL statement per invocation.")

    statement = statements[0]
    root_expression = _unwrap_root_expression(statement)
    if not isinstance(root_expression, exp.Query):
        raise QueryMapContractError(
            "catalogkit-query supports exactly one SELECT, INSERT ... SELECT, or CREATE ... AS SELECT statement per invocation."
        )
    return ParsedStatement(statement=statement, root_expression=root_expression, dialect=dialect)


def _unwrap_root_expression(statement: exp.Expression) -> exp.Expression:
    """Return the query-like root expression used for structure mapping."""
    if isinstance(statement, exp.Create):
        expression = statement.args.get("expression")
        if isinstance(expression, exp.Expression):
            return expression

    if isinstance(statement, exp.Insert):
        expression = statement.args.get("expression")
        if isinstance(expression, exp.Expression):
            return expression

    return statement
