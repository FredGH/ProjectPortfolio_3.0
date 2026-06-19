-- slv_mmm_weekly: ISO-week revenue from Olist joined with synthetic MMM spend and adstock transforms.
-- Grain: iso_week (unique).

{{ config(materialized='table', tags=['silver']) }}

WITH weekly_olist_revenue AS (
    -- Aggregate delivered Olist order revenue to ISO week.
    SELECT
        TO_VARCHAR(
            DATE_TRUNC('week', o.order_purchase_timestamp)::date,
            'IYYY-"W"IW'
        )                                                       AS iso_week,
        DATE_TRUNC('week', o.order_purchase_timestamp)::date    AS week_start_date,
        SUM(oi.price + oi.freight_value)                        AS olist_revenue
    FROM {{ ref('brz_olist_orders') }} AS o
    INNER JOIN {{ ref('brz_olist_order_items') }} AS oi USING (order_id)
    WHERE o.order_status = 'delivered'
    GROUP BY 1, 2
),

mmm_spend AS (
    SELECT
        iso_week,
        week_start_date,
        weekly_revenue   AS synthetic_revenue,
        tv_spend,
        paid_search_spend,
        social_spend,
        email_spend,
        display_spend,
        holiday_flag,
        black_friday_flag,
        competitor_index,
        avg_temperature
    FROM {{ ref('brz_mmm_weekly_spend') }}
),

joined AS (
    -- Prefer real Olist revenue; fall back to synthetic when Olist data is absent.
    SELECT
        s.iso_week,
        s.week_start_date,
        COALESCE(r.olist_revenue, s.synthetic_revenue) AS weekly_revenue,
        s.tv_spend,
        s.paid_search_spend,
        s.social_spend,
        s.email_spend,
        s.display_spend,
        s.holiday_flag,
        s.black_friday_flag,
        s.competitor_index,
        s.avg_temperature
    FROM mmm_spend AS s
    LEFT JOIN weekly_olist_revenue AS r USING (iso_week)
),

adstock AS (
    -- Single-period geometric adstock (α per channel) for MMM feature engineering.
    -- α values match the calibration in docs/mmm_synthetic_data_assumptions.md.
    SELECT
        iso_week,
        week_start_date,
        weekly_revenue,
        tv_spend,
        paid_search_spend,
        social_spend,
        email_spend,
        display_spend,
        tv_spend          + 0.50 * LAG(tv_spend, 1, 0)          OVER (ORDER BY iso_week) AS tv_spend_adstock,
        paid_search_spend + 0.30 * LAG(paid_search_spend, 1, 0) OVER (ORDER BY iso_week) AS paid_search_spend_adstock,
        social_spend      + 0.40 * LAG(social_spend, 1, 0)      OVER (ORDER BY iso_week) AS social_spend_adstock,
        email_spend       + 0.20 * LAG(email_spend, 1, 0)        OVER (ORDER BY iso_week) AS email_spend_adstock,
        display_spend     + 0.35 * LAG(display_spend, 1, 0)      OVER (ORDER BY iso_week) AS display_spend_adstock,
        holiday_flag,
        black_friday_flag,
        competitor_index,
        avg_temperature
    FROM joined
)

SELECT
    iso_week,
    week_start_date,
    weekly_revenue,
    tv_spend,
    paid_search_spend,
    social_spend,
    email_spend,
    display_spend,
    tv_spend_adstock,
    paid_search_spend_adstock,
    social_spend_adstock,
    email_spend_adstock,
    display_spend_adstock,
    holiday_flag,
    black_friday_flag,
    competitor_index,
    avg_temperature,
    CURRENT_TIMESTAMP()::timestamp_ntz AS _loaded_at
FROM adstock
ORDER BY iso_week
