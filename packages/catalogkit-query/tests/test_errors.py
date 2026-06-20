from __future__ import annotations

import pytest

from catalogkit.query import build_query_map
from catalogkit.query.errors import QueryMapContractError, QueryMapParseError


def test_invalid_sql_fails_loudly():
    with pytest.raises(QueryMapParseError):
        build_query_map("NOT VALID SQL AT ALL !!!", dialect="postgres")


def test_multiple_statements_fail_loudly():
    with pytest.raises(QueryMapContractError):
        build_query_map("SELECT * FROM customers; SELECT * FROM orders;", dialect="postgres")
