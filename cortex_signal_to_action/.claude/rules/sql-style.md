# SQL Style Rules

Enforces formatting, naming, and structure conventions for SQL and dbt models.

## Keyword Casing

- SQL keywords: **UPPERCASE** — `SELECT`, `FROM`, `WHERE`, `JOIN`, `GROUP BY`
- Function names: **UPPERCASE** — `COALESCE()`, `COUNT()`, `DATE_TRUNC()`
- Column and table names: **lowercase snake_case**
- Aliases: **lowercase snake_case**

```sql
-- Good
SELECT
    order_id,
    COUNT(*) AS order_count,
    COALESCE(discount_amount, 0) AS discount_amount
FROM orders
WHERE created_at >= '2024-01-01'

-- Bad
select order_id, count(*) as OrderCount from Orders
```

## Indentation & Layout

- 4 spaces per indent level (no tabs)
- Each selected column on its own line
- Commas at the **end** of the line (not leading)
- Closing keyword (`FROM`, `WHERE`, `GROUP BY`) at the same indent level as `SELECT`

```sql
SELECT
    user_id,
    email,
    created_at
FROM users
WHERE is_active = TRUE
ORDER BY created_at DESC
```

## CTEs

- Prefer CTEs over nested subqueries for anything beyond a trivial filter
- One CTE per logical step — name it for what it produces, not what it does
- Final `SELECT` at the bottom of the file reads from the last CTE

```sql
WITH active_users AS (
    SELECT *
    FROM {{ ref('stg_users') }}
    WHERE is_active = TRUE
),

user_orders AS (
    SELECT
        user_id,
        COUNT(*) AS order_count
    FROM {{ ref('stg_orders') }}
    GROUP BY user_id
)

SELECT
    u.user_id,
    u.email,
    COALESCE(o.order_count, 0) AS order_count
FROM active_users AS u
LEFT JOIN user_orders AS o USING (user_id)
```

## Naming Conventions

| Element | Convention | Example |
|---|---|---|
| Models | `snake_case` | `fct_orders`, `dim_users` |
| Staging models | `stg_<source>__<entity>` | `stg_stripe__payments` |
| Columns | `snake_case` | `created_at`, `order_id` |
| Primary keys | `<entity>_id` | `order_id`, `user_id` |
| Boolean columns | `is_` or `has_` prefix | `is_active`, `has_discount` |
| Date columns | `_at` suffix for timestamps, `_date` for dates | `created_at`, `order_date` |
| Surrogate keys | `<entity>_key` | `order_key` |

## Joins

- Always use explicit `JOIN` type — never implicit comma join
- Prefer `USING (col)` over `ON a.col = b.col` when column names match
- Alias all tables in multi-table queries
- Place the larger/base table on the left

```sql
-- Good
FROM orders AS o
LEFT JOIN customers AS c USING (customer_id)

-- Bad
FROM orders, customers WHERE orders.customer_id = customers.customer_id
```

## Comments

Add a header comment at the top of every model file describing what it produces and its grain:

```sql
-- fct_orders: one row per order, joined with customer and product dimensions.
-- Grain: order_id (unique).
```

Document each CTE with a one-line comment explaining what it produces:

```sql
-- Active customers who placed at least one order in the last 90 days.
WITH recent_active_customers AS (
    ...
),

-- Aggregate order totals per customer for the same window.
customer_order_totals AS (
    ...
)
```

Use inline comments to explain non-obvious logic — a filter with a business reason, a workaround for a data quality issue, or a calculation that would surprise a reader:

```sql
-- Exclude test accounts created by QA; they have no real orders.
WHERE customer_type != 'internal_test'

-- Use COALESCE here because legacy orders pre-2022 have NULL discount_amount
-- but should be treated as 0 for margin calculations.
COALESCE(discount_amount, 0) AS discount_amount
```

Do **not** comment self-explanatory clauses — a plain `WHERE is_active = TRUE` needs no comment.

## NULL Handling

- Be explicit: use `IS NULL` / `IS NOT NULL`, never `= NULL`
- Use `COALESCE()` to provide defaults rather than relying on implicit NULL propagation
- Document intentional NULLs with a comment

## dbt Conventions

- Always use `{{ ref('model_name') }}` for inter-model dependencies — never hardcode schema.table
- Use `{{ source('source_name', 'table_name') }}` only in staging models
- Model-level and column-level descriptions required in `schema.yml` for all marts and dimensions
- Config block at the top of the file:

```sql
{{ config(
    materialized='table',
    tags=['finance', 'daily']
) }}
```

- Use `{{ dbt_utils.generate_surrogate_key([...]) }}` for surrogate keys — never `MD5(CONCAT(...))`

## Formatting Tool

Use `sqlfluff` for automated formatting:
```bash
sqlfluff lint models/
sqlfluff fix models/
```

Dialect should be set in `.sqlfluff` config to match your warehouse (postgres, snowflake, bigquery).
