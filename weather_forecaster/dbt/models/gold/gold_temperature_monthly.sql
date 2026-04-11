-- Gold: monthly temperature distribution per capital city.
-- Combines two silver sources:
--   1. Live pipeline observations (silver_weather_observations via OpenWeather)
--   2. Historical backfill (silver_historical_weather via Open-Meteo Archive API)
--
-- When both sources have data for the same (city, year, month), the historical
-- source wins for past months to ensure ERA5 quality; the live source is used
-- for the current month where historical data is not yet available.
--
-- One row per (city_name, country_code, year, month).

{{ config(materialized='table') }}

-- ── Source 1: live pipeline (OpenWeather observations) ────────────────────────
WITH live_monthly AS (
    SELECT
        city_name                         AS city,
        country_code,
        CAST(YEAR(observed_at)  AS INTEGER) AS year,
        CAST(MONTH(observed_at) AS INTEGER) AS month,
        ROUND(AVG(temp_c),   2)             AS avg_temp_c,
        ROUND(MIN(temp_c),   2)             AS min_temp_c,
        ROUND(MAX(temp_c),   2)             AS max_temp_c,
        ROUND(AVG(humidity_pct), 1)         AS avg_humidity_pct,
        ROUND(AVG(wind_speed_ms), 2)        AS avg_wind_speed_ms,
        NULL::DOUBLE                        AS avg_cloud_cover_pct,
        NULL::DOUBLE                        AS total_precip_mm,
        COUNT(*)                            AS observation_count,
        'openweather-live'                  AS source
    FROM {{ ref('silver_weather_observations') }}
    WHERE observed_at >= TIMESTAMP '2000-01-01 00:00:00'
    GROUP BY city_name, country_code, YEAR(observed_at), MONTH(observed_at)
),

-- ── Source 2: Open-Meteo historical archive ────────────────────────────────
historical_monthly AS (
    SELECT
        city,
        country_code,
        year,
        month,
        avg_temp_c,
        min_temp_c,
        max_temp_c,
        avg_humidity_pct,
        avg_wind_speed_ms,
        avg_cloud_cover_pct,
        total_precip_mm,
        observation_count,
        source
    FROM {{ ref('silver_historical_weather') }}
),

-- ── Merge: historical wins for completed months, live fills current month ──
current_ym AS (
    SELECT YEAR(CURRENT_DATE) AS year, MONTH(CURRENT_DATE) AS month
),

merged AS (
    -- Historical rows for all months except the current one
    SELECT h.*
    FROM historical_monthly h, current_ym c
    WHERE NOT (h.year = c.year AND h.month = c.month)

    UNION ALL

    -- Live rows for the current month (always fresh)
    SELECT l.*
    FROM live_monthly l, current_ym c
    WHERE l.year = c.year AND l.month = c.month

    UNION ALL

    -- Live rows for months where historical data does not exist
    -- (e.g., cities not yet covered by the backfill)
    SELECT l.*
    FROM live_monthly l
    WHERE NOT EXISTS (
        SELECT 1 FROM historical_monthly h
        WHERE h.city = l.city
          AND h.country_code = l.country_code
          AND h.year  = l.year
          AND h.month = l.month
    )
    AND NOT EXISTS (
        SELECT 1 FROM current_ym c
        WHERE l.year = c.year AND l.month = c.month
    )
)

SELECT
    city         AS city_name,
    country_code,
    year,
    month,
    ROUND(avg_temp_c, 1)          AS avg_temp_c,
    ROUND(min_temp_c, 1)          AS min_temp_c,
    ROUND(max_temp_c, 1)          AS max_temp_c,
    ROUND(avg_humidity_pct, 0)    AS avg_humidity_pct,
    ROUND(avg_wind_speed_ms, 1)   AS avg_wind_speed_ms,
    ROUND(avg_cloud_cover_pct, 0) AS avg_cloud_cover_pct,
    ROUND(total_precip_mm, 1)     AS total_precip_mm,
    observation_count,
    source
FROM merged
ORDER BY city_name, year, month
