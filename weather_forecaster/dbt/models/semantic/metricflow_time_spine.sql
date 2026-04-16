-- MetricFlow time spine: a continuous daily date series required by the
-- dbt Semantic Layer for time-based metric calculations.
-- Covers 2018-01-01 → 2035-12-31 to span all historical + forecast data.

{{ config(
    materialized='table',
    static_analysis='off'
) }}

SELECT CAST(generate_series AS DATE) AS date_day
FROM generate_series(
    DATE '2018-01-01',
    DATE '2035-12-31',
    INTERVAL '1 day'
)
