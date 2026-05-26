{{ config(materialized='table') }}

-- Date spine: 2024-01-01 to 2025-12-31.
WITH date_spine AS (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('2024-01-01' as date)",
        end_date="cast('2026-01-01' as date)"
    ) }}
)

SELECT
    CAST(date_day AS DATE) AS date_id,
    date_day               AS full_date,
    EXTRACT(YEAR  FROM date_day)::INT  AS year,
    EXTRACT(MONTH FROM date_day)::INT  AS month,
    EXTRACT(DAY   FROM date_day)::INT  AS day,
    EXTRACT(DOW   FROM date_day)::INT  AS day_of_week,  -- 0=Sunday
    EXTRACT(DOY   FROM date_day)::INT  AS day_of_year,
    EXTRACT(WEEK  FROM date_day)::INT  AS week_of_year,
    EXTRACT(QUARTER FROM date_day)::INT AS quarter,
    TO_CHAR(date_day, 'Month')         AS month_name,
    TO_CHAR(date_day, 'Day')           AS day_name,
    EXTRACT(DOW FROM date_day) IN (0, 6) AS is_weekend,
    NOT (EXTRACT(DOW FROM date_day) IN (0, 6)) AS is_trading_day
FROM date_spine
