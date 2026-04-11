-- Staging model for Open-Meteo historical monthly weather data.
-- Source: staging.historical_weather_monthly (loaded by historical_backfill asset).
-- One row per (city, country_code, year, month).

WITH source AS (
    SELECT * FROM {{ source('bronze', 'historical_weather_monthly') }}
)

SELECT
    city,
    country,
    country_code,
    CAST(lat  AS DOUBLE) AS lat,
    CAST(lon  AS DOUBLE) AS lon,
    CAST(year  AS INTEGER) AS year,
    CAST(month AS INTEGER) AS month,
    CAST(avg_temp_c          AS DOUBLE) AS avg_temp_c,
    CAST(min_temp_c          AS DOUBLE) AS min_temp_c,
    CAST(max_temp_c          AS DOUBLE) AS max_temp_c,
    CAST(avg_humidity_pct    AS DOUBLE) AS avg_humidity_pct,
    CAST(avg_wind_speed_ms   AS DOUBLE) AS avg_wind_speed_ms,
    CAST(avg_cloud_cover_pct AS DOUBLE) AS avg_cloud_cover_pct,
    CAST(total_precip_mm     AS DOUBLE) AS total_precip_mm,
    CAST(observation_count   AS INTEGER) AS observation_count,
    source
FROM source
