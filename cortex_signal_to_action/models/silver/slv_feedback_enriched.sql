-- slv_feedback_enriched: Olist order reviews translated, sentiment-scored, and theme-extracted via Snowflake Cortex.
-- Grain: review_id (unique). Incremental — new review_ids merged each run.

{{ config(
    materialized='incremental',
    unique_key='review_id',
    incremental_strategy='merge',
    tags=['silver', 'cortex']
) }}

WITH source_reviews AS (
    -- All reviews from bronze; on incremental runs, only unprocessed review_ids.
    SELECT
        review_id,
        order_id,
        review_score,
        review_comment_message,
        review_comment_title,
        review_creation_date,
        review_answer_timestamp
    FROM {{ ref('brz_olist_order_reviews') }}
    {% if is_incremental() %}
        WHERE review_id NOT IN (SELECT review_id FROM {{ this }})
    {% endif %}
),

translated AS (
    -- Translate review text to English; source language auto-detected ('' = auto).
    -- COALESCE preserves original text if TRANSLATE returns NULL (e.g. very short inputs).
    SELECT
        review_id,
        order_id,
        review_score,
        review_comment_message,
        review_comment_title,
        review_creation_date,
        review_answer_timestamp,
        COALESCE(
            SNOWFLAKE.CORTEX.TRANSLATE(review_comment_message, '', 'en'),
            review_comment_message
        ) AS translated_review
    FROM source_reviews
),

sentiment_raw_scored AS (
    -- Score translated text with AI_SENTIMENT across four e-commerce aspects.
    -- Macro guards against NULL / blank input and returns raw VARIANT.
    SELECT
        review_id,
        order_id,
        review_score,
        review_comment_message,
        review_comment_title,
        review_creation_date,
        review_answer_timestamp,
        translated_review,
        {{ cortex_sentiment(
            'translated_review',
            "ARRAY_CONSTRUCT('product_quality', 'delivery', 'customer_service', 'price_value')"
        ) }} AS sentiment_raw
    FROM translated
),

theme_raw_extracted AS (
    -- Extract primary theme and key phrase via CORTEX.COMPLETE.
    -- Prompt constrains the model to return valid JSON for safe TRY_PARSE_JSON downstream.
    SELECT
        review_id,
        order_id,
        review_score,
        review_comment_message,
        review_comment_title,
        review_creation_date,
        review_answer_timestamp,
        translated_review,
        sentiment_raw,
        CASE
            WHEN translated_review IS NULL OR TRIM(translated_review) = ''
                THEN NULL
            ELSE SNOWFLAKE.CORTEX.COMPLETE(
                'claude-sonnet-4-20250514',
                CONCAT(
                    'Extract a primary theme and a single key phrase from this customer review. ',
                    'Respond ONLY with valid JSON using exactly this structure: ',
                    '{"theme": "<one of: product_quality, delivery, customer_service, price_value, packaging, other>", ',
                    '"key_phrase": "<3-7 word phrase>"}. ',
                    'Review: ', translated_review
                )
            )
        END AS theme_raw
    FROM sentiment_raw_scored
)

SELECT
    review_id,
    order_id,
    review_score,
    review_comment_message,
    review_comment_title,
    review_creation_date,
    review_answer_timestamp,
    translated_review,
    -- Sentiment score and label parsed from AI_SENTIMENT VARIANT
    TRY_CAST(sentiment_raw:score::varchar AS float)                              AS sentiment_score,
    UPPER(COALESCE(sentiment_raw:sentiment::varchar, 'UNKNOWN'))                 AS sentiment_label,
    -- Aspect scores parsed from AI_SENTIMENT aspects sub-object
    TRY_CAST(sentiment_raw:aspects:product_quality:score::varchar AS float)      AS aspect_product_quality,
    TRY_CAST(sentiment_raw:aspects:delivery:score::varchar AS float)             AS aspect_delivery,
    TRY_CAST(sentiment_raw:aspects:customer_service:score::varchar AS float)     AS aspect_customer_service,
    TRY_CAST(sentiment_raw:aspects:price_value:score::varchar AS float)          AS aspect_price_value,
    -- Theme classification parsed from CORTEX.COMPLETE JSON response
    TRY_PARSE_JSON(theme_raw)                                                    AS theme_json,
    COALESCE(TRY_PARSE_JSON(theme_raw):theme::varchar, 'other')                 AS theme,
    TRY_PARSE_JSON(theme_raw):key_phrase::varchar                                AS key_phrase,
    -- Churn risk signal: dissatisfied customers (score <= 2) flagged for NBA actions
    IFF(review_score <= 2, 1, 0)                                                 AS churn_risk_flag,
    CURRENT_TIMESTAMP()::timestamp_ntz                                           AS _loaded_at
FROM theme_raw_extracted
