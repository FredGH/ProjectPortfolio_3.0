-- mrt_mmm_attribution: channel-level revenue attribution and ROI per ISO week.
-- Grain: (iso_week, channel). ~130 weeks × 5 channels = ~650 rows.
-- Attribution uses adstock-weighted proportional allocation with per-channel efficiency
-- multipliers calibrated to the synthetic DGP. In production, replace the efficiency
-- multipliers with Ridge regression coefficients trained via Snowpark ML
-- (snowflake.ml.modeling.linear_model.Ridge) on slv_mmm_weekly.

{{ config(materialized='table', tags=['gold']) }}

WITH mmm AS (
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
        black_friday_flag
    FROM {{ ref('slv_mmm_weekly') }}
),

-- UNPIVOT channel spend columns to long format.
channels AS (
    SELECT iso_week, week_start_date, weekly_revenue, holiday_flag, black_friday_flag,
        'tv'          AS channel,
        tv_spend           AS spend,
        tv_spend_adstock   AS adstock_spend
    FROM mmm
    UNION ALL
    SELECT iso_week, week_start_date, weekly_revenue, holiday_flag, black_friday_flag,
        'paid_search' AS channel,
        paid_search_spend           AS spend,
        paid_search_spend_adstock   AS adstock_spend
    FROM mmm
    UNION ALL
    SELECT iso_week, week_start_date, weekly_revenue, holiday_flag, black_friday_flag,
        'social'      AS channel,
        social_spend           AS spend,
        social_spend_adstock   AS adstock_spend
    FROM mmm
    UNION ALL
    SELECT iso_week, week_start_date, weekly_revenue, holiday_flag, black_friday_flag,
        'email'       AS channel,
        email_spend           AS spend,
        email_spend_adstock   AS adstock_spend
    FROM mmm
    UNION ALL
    SELECT iso_week, week_start_date, weekly_revenue, holiday_flag, black_friday_flag,
        'display'     AS channel,
        display_spend           AS spend,
        display_spend_adstock   AS adstock_spend
    FROM mmm
),

-- Channel efficiency multipliers proxy Ridge regression coefficients.
-- Calibrated to synthetic DGP: email and paid_search have highest response rate;
-- display is weakest per unit spend.
with_efficiency AS (
    SELECT
        iso_week,
        week_start_date,
        channel,
        spend,
        adstock_spend,
        weekly_revenue,
        holiday_flag,
        black_friday_flag,
        CASE channel
            WHEN 'tv'          THEN 1.2
            WHEN 'paid_search' THEN 1.5
            WHEN 'social'      THEN 1.1
            WHEN 'email'       THEN 1.8
            WHEN 'display'     THEN 0.9
        END                           AS efficiency_multiplier,
        adstock_spend * CASE channel
            WHEN 'tv'          THEN 1.2
            WHEN 'paid_search' THEN 1.5
            WHEN 'social'      THEN 1.1
            WHEN 'email'       THEN 1.8
            WHEN 'display'     THEN 0.9
        END                           AS weighted_adstock
    FROM channels
),

-- Total weighted adstock per week — denominator for proportional share.
weekly_totals AS (
    SELECT
        iso_week,
        SUM(weighted_adstock) AS total_weighted_adstock
    FROM with_efficiency
    GROUP BY iso_week
),

final AS (
    SELECT
        w.iso_week,
        w.week_start_date,
        w.channel,
        w.spend,
        w.adstock_spend,
        w.weekly_revenue,
        w.holiday_flag,
        w.black_friday_flag,
        -- Channel's proportional share of weekly revenue weighted by adstock efficiency.
        ROUND(
            (w.weighted_adstock / NULLIF(t.total_weighted_adstock, 0))
                * w.weekly_revenue,
            2
        )                             AS attributed_revenue,
        -- ROI: attributed revenue per BRL spent; 0.0 when spend = 0 (never NULL).
        ROUND(
            COALESCE(
                ((w.weighted_adstock / NULLIF(t.total_weighted_adstock, 0)) * w.weekly_revenue)
                    / NULLIF(w.spend, 0),
                0.0
            ),
            4
        )                             AS roi,
        -- Marginal ROI: incremental revenue per additional BRL of spend in this week.
        ROUND(
            w.efficiency_multiplier
                * w.weekly_revenue
                / NULLIF(t.total_weighted_adstock, 0),
            6
        )                             AS marginal_roi
    FROM with_efficiency AS w
    INNER JOIN weekly_totals AS t USING (iso_week)
)

SELECT
    iso_week,
    week_start_date,
    channel,
    spend,
    adstock_spend,
    weekly_revenue,
    attributed_revenue,
    roi,
    marginal_roi,
    holiday_flag,
    black_friday_flag,
    CURRENT_TIMESTAMP()::timestamp_ntz AS _loaded_at
FROM final
ORDER BY iso_week, channel
