# SQL Testing Rules

Defines how tests should be written for SQL and dbt models.

## Test Runner

```bash
dbt test                          # run all tests
dbt test --select model_name      # run tests for a specific model
dbt test --select tag:daily       # run tests by tag
```

## Schema Tests (schema.yml)

Every model must have a `schema.yml` entry. At minimum, define tests on the primary key and any foreign keys.

### Required on every model
```yaml
models:
  - name: fct_orders
    description: "One row per order."
    columns:
      - name: order_id
        description: "Primary key."
        tests:
          - unique
          - not_null
```

### Required on foreign keys
```yaml
      - name: customer_id
        tests:
          - not_null
          - relationships:
              to: ref('dim_customers')
              field: customer_id
```

### Required on categorical columns with a fixed set of values
```yaml
      - name: status
        tests:
          - accepted_values:
              values: ['pending', 'completed', 'cancelled']
```

## Coverage Rules

| Layer | Required tests |
|---|---|
| Staging | `unique` + `not_null` on PK only — minimal, fast |
| Dimensions | PK tests + `relationships` on all FKs + `accepted_values` on status/type columns |
| Facts | PK tests + `relationships` on all FKs + non-negative checks on measures |
| Marts | PK tests + key business rule assertions via singular tests |

## Singular Tests (custom SQL assertions)

For business logic that generic schema tests cannot express. Place in `tests/` directory. A test passes when it returns **zero rows** — any returned row is a failure.

```sql
-- tests/assert_orders_total_matches_line_items.sql
-- Every order total must equal the sum of its line items.

SELECT
    o.order_id,
    o.total_amount,
    SUM(li.line_amount) AS calculated_total
FROM {{ ref('fct_orders') }} AS o
LEFT JOIN {{ ref('fct_order_line_items') }} AS li USING (order_id)
GROUP BY o.order_id, o.total_amount
HAVING o.total_amount != SUM(li.line_amount)
```

Name singular tests descriptively: `assert_<what_must_be_true>.sql`.

## dbt-expectations

Use `dbt-expectations` for richer assertions beyond built-in tests:

```yaml
      - name: amount
        tests:
          - dbt_expectations.expect_column_values_to_be_between:
              min_value: 0
              max_value: 1000000
          - dbt_expectations.expect_column_values_to_not_be_null

      - name: email
        tests:
          - dbt_expectations.expect_column_values_to_match_regex:
              regex: '^[^@]+@[^@]+\.[^@]+$'
```

## Freshness Tests

Define source freshness expectations in `sources.yml` for every raw source:

```yaml
sources:
  - name: stripe
    freshness:
      warn_after: {count: 12, period: hour}
      error_after: {count: 24, period: hour}
    loaded_at_field: _loaded_at
    tables:
      - name: payments
```

Run with:
```bash
dbt source freshness
```

## Test Severity

Use `warn` severity for non-blocking checks (monitoring), `error` (default) for blocking checks:

```yaml
tests:
  - unique:
      severity: error       # blocks pipeline
  - dbt_expectations.expect_column_proportion_of_unique_values_to_be_between:
      min_value: 0.95
      severity: warn        # alerts but does not block
```

## What NOT to Do

- Do not write singular tests that duplicate what schema tests already cover
- Do not skip PK tests on any model — `unique` + `not_null` is the minimum everywhere
- Do not rely on application-layer validation instead of data tests — test at the data layer
- Do not hardcode dates in singular tests — use `{{ var('start_date') }}` or `current_date`
