-- assert_no_untranslated.sql: fewer than 2% of non-null translated_review rows should contain
-- common Portuguese stopwords, indicating untranslated text slipped through CORTEX.TRANSLATE.
-- Stopwords checked: não, que, para, com, uma, por, mas.
-- Returns a summary row only when the failure rate meets or exceeds 2% — zero rows = test passes.

WITH stopword_check AS (
    SELECT
        COUNT(*)          AS total_reviews,
        COUNT_IF(
            REGEXP_ILIKE(
                translated_review,
                '.*(\\bnão\\b|\\bque\\b|\\bpara\\b|\\bcom\\b|\\buma\\b|\\bpor\\b|\\bmas\\b).*'
            )
        )                 AS portuguese_rows
    FROM {{ ref('slv_feedback_enriched') }}
    WHERE translated_review IS NOT NULL
)

SELECT
    total_reviews,
    portuguese_rows,
    ROUND(portuguese_rows * 100.0 / NULLIF(total_reviews, 0), 2) AS portuguese_pct
FROM stopword_check
WHERE portuguese_rows * 100.0 / NULLIF(total_reviews, 0) >= 2.0
