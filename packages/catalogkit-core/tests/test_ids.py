import pytest

from catalogkit.core import CanonicalIdError, column_id, cte_id, normalize_identifier, table_id


def test_qualified_identifier_normalizes_case_and_quotes():
    assert normalize_identifier("Analytics.Orders") == "analytics.orders"
    assert normalize_identifier('"analytics"."orders"') == "analytics.orders"
    assert normalize_identifier("`analytics`.`orders`") == "analytics.orders"


def test_table_and_column_ids_are_deterministic():
    assert table_id("Analytics.Orders") == "table:analytics.orders"
    assert table_id('"analytics"."orders"') == "table:analytics.orders"
    assert cte_id("Customer_Rollup") == "cte:customer_rollup"
    assert column_id('"analytics"."orders"', '"ID"') == "column:analytics.orders.id"


def test_invalid_identifier_fails_loudly():
    with pytest.raises(CanonicalIdError):
        normalize_identifier("")

    with pytest.raises(CanonicalIdError):
        table_id("analytics..orders")
