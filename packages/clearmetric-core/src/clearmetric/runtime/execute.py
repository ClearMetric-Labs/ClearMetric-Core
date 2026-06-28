"""DuckDB query execution."""

from __future__ import annotations

from clearmetric.core.errors import QueryExecutionError


def execute_query(sql: str, *, connection=None) -> list[dict]:
    """Execute SQL against DuckDB and return rows as dicts."""
    try:
        import duckdb
    except ImportError as exc:
        raise QueryExecutionError(
            "duckdb is required for query execution: pip install 'clearmetric-core[runtime]'"
        ) from exc

    conn = connection or duckdb.connect(database=":memory:")
    try:
        relation = conn.execute(sql)
        columns = [col[0] for col in relation.description]
        return [dict(zip(columns, row, strict=True)) for row in relation.fetchall()]
    except Exception as exc:
        raise QueryExecutionError(f"query execution failed: {exc}") from exc
