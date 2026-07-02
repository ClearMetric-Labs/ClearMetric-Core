from __future__ import annotations

from clearmetric.lineage.sql_analyzer import (
    defining_value_expression,
    filter_value_lineage_refs,
    lineage_output_map,
)
from sqlglot import exp, parse_one
from sqlglot.lineage import Node as SqlglotLineageNode
from sqlglot.lineage import lineage


def test_value_columns_excludes_case_predicate_columns():
    expression = parse_one(
        "sum(case when raw_payments.payment_method = 'credit_card' "
        "then raw_payments.amount else 0 end)",
        dialect="postgres",
    )
    root = SqlglotLineageNode(
        name="credit_card_amount",
        expression=expression,
        source=expression,
        downstream=[
            SqlglotLineageNode(
                name="raw_payments.amount",
                expression=exp.column("amount", table="raw_payments"),
                source=expression,
            ),
            SqlglotLineageNode(
                name="raw_payments.payment_method",
                expression=exp.column("payment_method", table="raw_payments"),
                source=expression,
            ),
        ],
    )

    filtered = filter_value_lineage_refs(
        root,
        {"raw_payments.amount", "raw_payments.payment_method"},
        dialect="postgres",
    )

    assert filtered == {"raw_payments.amount"}


def test_defining_value_expression_follows_cte_projection_to_case():
    sql = """
    with order_payments as (
        select
            sum(case when payment_method = 'credit_card' then amount else 0 end)
                as credit_card_amount
        from payments
        group by order_id
    )
    select order_payments.credit_card_amount
    from order_payments
    """
    output_map = lineage_output_map(
        sql,
        schema={"payments": {"amount": "amount", "payment_method": "payment_method"}},
        sources=None,
        dialect="postgres",
    )
    root = output_map["credit_card_amount"]

    defining = defining_value_expression(root)

    assert any(isinstance(node, exp.Case) for node in defining.walk())

    filtered = filter_value_lineage_refs(
        root,
        {"payments.amount", "payments.payment_method"},
        dialect="postgres",
    )

    assert filtered == {"payments.amount"}
