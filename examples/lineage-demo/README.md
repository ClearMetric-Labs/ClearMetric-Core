# Lineage Demo

Self-contained plain-SQL lineage example: three SQL models (`orders_base` → `customer_totals` → `customers_report`) merged with a **realistic warehouse metadata export** (Shopify-style raw landing zone: 22 tables, 280+ columns). Only `raw_orders` is referenced by the SQL pipeline.

Notebooks can load this folder from the repository or fetch it from GitHub (see `examples/notebooks/_paths.py`).

## Prerequisites

```bash
pip install clearmetric-core
cd examples/lineage-demo
```

## Commands

```bash
cm scan
cm compile --format json > graph.json
cm compile --format catalog > catalog.json
cm impact customers_report.customer_lifetime_value --upstream --format json
cm impact orders_base.amount --downstream --format json
cm clean
cm contract graph.json
```

## Impact selections

**Upstream** — `customers_report.customer_lifetime_value` → 3 related columns through `raw_orders.amount`

**Downstream** — `orders_base.amount` → `customer_totals.total_amount`, `customers_report.customer_lifetime_value`
