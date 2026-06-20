WITH active_customers AS (
    SELECT id
    FROM public.customers
    WHERE is_active = true
)
SELECT *
FROM active_customers
