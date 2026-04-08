-- Silver: enriched 3-hour forecast intervals.
-- Adds derived labels (wind description, cloud description) and a
-- hours_from_now convenience column for easy downstream filtering.

WITH forecast AS (
    SELECT * FROM {{ ref('stg_weather_forecast') }}
)

SELECT
    -- Location
    lat,
    lon,

    -- Time
    forecast_at,
    forecast_dt_txt,
    DATEDIFF('hour', NOW(), forecast_at) AS hours_from_now,

    -- Temperature
    temp_c,
    feels_like_c,

    -- Atmosphere
    humidity_pct,
    pressure_hpa,

    -- Wind
    wind_speed_ms,
    wind_direction_deg,

    -- Cloud cover
    cloud_cover_pct,

    -- Derived labels
    CASE
        WHEN cloud_cover_pct < 25  THEN 'clear'
        WHEN cloud_cover_pct < 50  THEN 'partly cloudy'
        WHEN cloud_cover_pct < 85  THEN 'mostly cloudy'
        ELSE 'overcast'
    END AS cloud_description,
    CASE
        WHEN wind_speed_ms < 0.5  THEN 'calm'
        WHEN wind_speed_ms < 5.5  THEN 'light breeze'
        WHEN wind_speed_ms < 10.8 THEN 'moderate breeze'
        WHEN wind_speed_ms < 17.2 THEN 'strong breeze'
        ELSE 'gale or above'
    END AS wind_description,

    -- Metadata
    fetched_at

FROM forecast
