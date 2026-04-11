-- Silver: historical monthly weather aggregates from the Open-Meteo Archive API.
-- Passes through all columns from the bronze staging model unchanged — the
-- aggregation was already done in Python before loading to DuckDB.
-- One row per (city, country_code, year, month).

SELECT
    city,
    country,
    country_code,
    lat,
    lon,
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
FROM {{ ref('stg_historical_weather') }}
