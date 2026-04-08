-- Staging model for weather_forecast bronze table.
-- Each row is a 3-hour forecast interval returned by /data/2.5/forecast.

WITH source AS (
    SELECT * FROM {{ source('bronze', 'weather_forecast') }}
),

renamed AS (
    SELECT
        -- Location
        lat,
        lon,

        -- Forecast interval
        TO_TIMESTAMP(dt)                            AS forecast_at,
        dt_txt                                      AS forecast_dt_txt,

        -- Temperature (metric — °C)
        CAST(main_temp       AS DOUBLE)             AS temp_c,
        CAST(main_feels_like AS DOUBLE)             AS feels_like_c,
        CAST(main_humidity   AS INTEGER)            AS humidity_pct,
        CAST(main_pressure   AS INTEGER)            AS pressure_hpa,

        -- Wind
        CAST(wind_speed      AS DOUBLE)             AS wind_speed_ms,
        CAST(wind_deg        AS INTEGER)            AS wind_direction_deg,

        -- Cloud cover
        CAST(clouds_all      AS INTEGER)            AS cloud_cover_pct,

        -- Fetch metadata
        _fetched_at                                 AS fetched_at

    FROM source
)

SELECT * FROM renamed
