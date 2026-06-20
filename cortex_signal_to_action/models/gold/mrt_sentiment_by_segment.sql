-- mrt_sentiment_by_segment: review sentiment and theme aggregated by customer segment,
-- product category, and ISO review week.
-- Grain: (segment_id, product_category, iso_week).

{{ config(materialized='table', tags=['gold']) }}

WITH feedback AS (
    SELECT
        f.review_id,
        f.order_id,
        f.review_score,
        f.sentiment_score,
        f.sentiment_label,
        f.theme,
        f.churn_risk_flag,
        TO_VARCHAR(
            DATE_TRUNC('week', f.review_creation_date)::date,
            'IYYY-"W"IW'
        ) AS iso_week
    FROM {{ ref('slv_feedback_enriched') }} AS f
    WHERE f.sentiment_score IS NOT NULL
),

-- Resolve order → customer → segment via the customer dimension.
order_to_segment AS (
    SELECT
        o.order_id,
        seg.segment_id,
        seg.segment_label
    FROM {{ ref('brz_olist_orders') }} AS o
    INNER JOIN {{ ref('brz_olist_customers') }} AS c USING (customer_id)
    INNER JOIN {{ ref('mrt_customer_segments') }} AS seg USING (customer_unique_id)
),

-- Resolve order → product_category; take the first item per order to avoid fan-out.
order_to_category AS (
    SELECT
        oi.order_id,
        COALESCE(p.product_category_name, 'unknown') AS product_category
    FROM {{ ref('brz_olist_order_items') }} AS oi
    LEFT JOIN {{ ref('brz_olist_products') }} AS p USING (product_id)
    QUALIFY ROW_NUMBER() OVER (PARTITION BY oi.order_id ORDER BY oi.order_item_id) = 1
),

enriched AS (
    SELECT
        f.review_id,
        f.review_score,
        f.sentiment_score,
        f.sentiment_label,
        f.theme,
        f.churn_risk_flag,
        f.iso_week,
        ots.segment_id,
        ots.segment_label,
        COALESCE(otc.product_category, 'unknown') AS product_category
    FROM feedback AS f
    INNER JOIN order_to_segment AS ots USING (order_id)
    LEFT JOIN order_to_category AS otc USING (order_id)
),

-- Aggregate all metrics per (segment_id, product_category, iso_week).
aggregated AS (
    SELECT
        segment_id,
        segment_label,
        product_category,
        iso_week,
        COUNT(*)                                                              AS total_reviews,
        ROUND(AVG(sentiment_score), 4)                                        AS avg_sentiment_score,
        ROUND(STDDEV(sentiment_score), 4)                                     AS stddev_sentiment_score,
        ROUND(AVG(review_score), 2)                                           AS avg_review_score,
        SUM(CASE WHEN sentiment_label = 'NEGATIVE' THEN 1 ELSE 0 END)        AS negative_reviews,
        ROUND(
            SUM(CASE WHEN sentiment_label = 'NEGATIVE' THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0)::float,
            4
        )                                                                     AS negative_review_pct,
        SUM(churn_risk_flag)                                                  AS churn_risk_reviews
    FROM enriched
    GROUP BY segment_id, segment_label, product_category, iso_week
),

-- Dominant theme: most frequent theme per (segment, category, week) bucket.
-- QUALIFY picks the top-ranked theme row after GROUP BY; COUNT(*) refers to the aggregate.
theme_dominant AS (
    SELECT
        segment_id,
        product_category,
        iso_week,
        theme AS dominant_theme
    FROM enriched
    GROUP BY segment_id, product_category, iso_week, theme
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY segment_id, product_category, iso_week
        ORDER BY COUNT(*) DESC, theme
    ) = 1
)

SELECT
    a.segment_id,
    a.segment_label,
    a.product_category,
    a.iso_week,
    a.total_reviews,
    a.avg_sentiment_score,
    a.stddev_sentiment_score,
    a.avg_review_score,
    a.negative_reviews,
    a.negative_review_pct,
    a.churn_risk_reviews,
    td.dominant_theme,
    CURRENT_TIMESTAMP()::timestamp_ntz AS _loaded_at
FROM aggregated AS a
INNER JOIN theme_dominant AS td
    ON a.segment_id   = td.segment_id
    AND a.product_category = td.product_category
    AND a.iso_week    = td.iso_week
