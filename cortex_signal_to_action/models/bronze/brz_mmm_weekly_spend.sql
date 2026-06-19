-- brz_mmm_weekly_spend: typed MMM spend from seed. Grain: iso_week (unique).

{{ config(materialized='table', tags=['bronze']) }}

SELECT
    iso_week,
    week_start_date,
    weekly_revenue,
    tv_spend,
    paid_search_spend,
    social_spend,
    email_spend,
    display_spend,
    holiday_flag,
    black_friday_flag,
    competitor_index,
    avg_temperature,
    CURRENT_TIMESTAMP()::timestamp_ntz AS _loaded_at
FROM {{ ref('olist_mmm_weekly_spend') }}
