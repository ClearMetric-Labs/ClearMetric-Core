WITH raw_orders AS (
    SELECT customer_id, amount
    FROM analytics.orders
),
customer_totals AS (
    SELECT customer_id, SUM(amount) AS total_amount
    FROM raw_orders
    GROUP BY customer_id
)
SELECT *
FROM customer_totals
