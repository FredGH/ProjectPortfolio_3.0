-- brz_olist_order_reviews: typed customer reviews for delivered orders. Grain: review_id (unique).

{{ config(materialized='table', tags=['bronze']) }}

SELECT
    review_id::varchar(36)                     AS review_id,
    order_id::varchar(36)                      AS order_id,
    review_score::integer                      AS review_score,
    TRY_CAST(review_comment_title AS varchar(500))    AS review_comment_title,
    TRY_CAST(review_comment_message AS varchar(2000)) AS review_comment_message,
    review_creation_date::timestamp_ntz        AS review_creation_date,
    review_answer_timestamp::timestamp_ntz     AS review_answer_timestamp,
    CURRENT_TIMESTAMP()::timestamp_ntz         AS _loaded_at
FROM {{ source('olist', 'olist_order_reviews') }}
