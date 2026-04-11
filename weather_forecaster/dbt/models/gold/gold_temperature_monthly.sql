-- Gold: monthly temperature distribution per capital city.
-- Aggregates silver observations by (city_name, country_code, year, month).
-- Filters out epoch-zero artifacts (observed_at = 1970-01-01).
-- Materialized as a table so the API can query it efficiently.

{{ config(materialized='table') }}

SELECT
    city_name,
    country_code,
    CAST(YEAR(observed_at)  AS INTEGER) AS year,
    CAST(MONTH(observed_at) AS INTEGER) AS month,
    ROUND(AVG(temp_c),   1)             AS avg_temp_c,
    ROUND(MIN(temp_c),   1)             AS min_temp_c,
    ROUND(MAX(temp_c),   1)             AS max_temp_c,
    ROUND(AVG(humidity_pct), 0)         AS avg_humidity_pct,
    COUNT(*)                            AS observation_count

FROM {{ ref('silver_weather_observations') }}

-- Exclude epoch-zero artifacts (dt=0 returned by some API responses)
WHERE observed_at >= TIMESTAMP '2000-01-01 00:00:00'

GROUP BY
    city_name,
    country_code,
    YEAR(observed_at),
    MONTH(observed_at)

ORDER BY city_name, year, month
