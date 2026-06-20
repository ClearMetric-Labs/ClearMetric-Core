WITH base_orders AS (
    SELECT
        o.customer_id,
        o.order_id,
        o.order_total,
        o.created_at
    FROM analytics.orders o
    WHERE o.status = 'completed'
),
customer_rollup AS (
    SELECT
        bo.customer_id,
        COUNT(*) AS order_count,
        SUM(bo.order_total) AS lifetime_value
    FROM base_orders bo
    GROUP BY bo.customer_id
),
ranked_customers AS (
    SELECT
        c.customer_name,
        cr.lifetime_value,
        ROW_NUMBER() OVER (ORDER BY cr.lifetime_value DESC) AS customer_rank
    FROM analytics.customers c
    LEFT JOIN customer_rollup cr
        ON c.customer_id = cr.customer_id
)
SELECT *
FROM ranked_customers
WHERE customer_rank <= 100
