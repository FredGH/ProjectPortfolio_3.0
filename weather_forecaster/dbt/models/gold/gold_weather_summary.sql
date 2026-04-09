-- Gold: latest weather per location + 24-hour forecast summary.
-- Reads from silver — never directly from bronze or sources.
-- One row per location (most recent observation only).

WITH latest_obs AS (
    SELECT
        city_name,
        country_code,
        state,
        lat,
        lon,
        utc_offset_seconds,
        observed_at,
        fetched_at,
        temp_c,
        feels_like_c,
        temp_min_c,
        temp_max_c,
        daily_temp_range_c,
        humidity_pct,
        pressure_hpa,
        visibility_m,
        wind_speed_ms,
        wind_direction_deg,
        cloud_cover_pct,
        cloud_description,
        wind_description,
        sunrise_at,
        sunset_at,
        ROW_NUMBER() OVER (PARTITION BY lat, lon ORDER BY fetched_at DESC) AS rn
    FROM {{ ref('silver_weather_observations') }}
),

current_weather AS (
    SELECT
        city_name,
        country_code,
        state,
        lat,
        lon,
        utc_offset_seconds,
        observed_at,
        fetched_at,
        temp_c,
        feels_like_c,
        temp_min_c,
        temp_max_c,
        daily_temp_range_c,
        humidity_pct,
        pressure_hpa,
        visibility_m,
        wind_speed_ms,
        wind_direction_deg,
        cloud_cover_pct,
        cloud_description,
        wind_description,
        sunrise_at,
        sunset_at
    FROM latest_obs
    WHERE rn = 1
),

forecast_24h AS (
    SELECT
        lat,
        lon,
        AVG(temp_c)              AS avg_temp_c_24h,
        MAX(temp_c)              AS max_temp_c_24h,
        MIN(temp_c)              AS min_temp_c_24h,
        AVG(wind_speed_ms)       AS avg_wind_speed_ms_24h,
        AVG(cloud_cover_pct)     AS avg_cloud_cover_pct_24h,
        MAX(humidity_pct)        AS max_humidity_pct_24h,
        -- Most frequent cloud / wind description in the window
        mode(cloud_description) AS predominant_cloud_description,
        mode(wind_description)  AS predominant_wind_description,
        MIN(forecast_at)         AS forecast_window_start,
        MAX(forecast_at)         AS forecast_window_end,
        COUNT(*)                 AS forecast_interval_count
    FROM {{ ref('silver_forecast_intervals') }}
    WHERE hours_from_now BETWEEN 0 AND 24
    GROUP BY lat, lon
)

SELECT
    -- Identity
    c.city_name,
    c.country_code,
    c.state,
    c.lat,
    c.lon,
    c.utc_offset_seconds,

    -- Current observation
    c.observed_at,
    c.fetched_at,
    c.temp_c                         AS current_temp_c,
    c.feels_like_c,
    c.temp_min_c,
    c.temp_max_c,
    c.daily_temp_range_c,
    c.humidity_pct,
    c.pressure_hpa,
    c.visibility_m,
    c.wind_speed_ms,
    c.wind_direction_deg,
    c.cloud_cover_pct,
    c.cloud_description              AS current_cloud_description,
    c.wind_description               AS current_wind_description,
    c.sunrise_at,
    c.sunset_at,

    -- 24-hour forecast summary
    f.avg_temp_c_24h,
    f.max_temp_c_24h,
    f.min_temp_c_24h,
    f.avg_wind_speed_ms_24h,
    f.avg_cloud_cover_pct_24h,
    f.max_humidity_pct_24h,
    f.predominant_cloud_description,
    f.predominant_wind_description,
    f.forecast_window_start,
    f.forecast_window_end,
    f.forecast_interval_count

FROM current_weather AS c
LEFT JOIN forecast_24h AS f USING (lat, lon)
