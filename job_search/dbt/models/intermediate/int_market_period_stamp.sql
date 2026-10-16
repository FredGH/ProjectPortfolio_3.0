-- int_market_period_stamp: the panel and matrix stamps every trend mart reads
-- (PLAN.md Step 21a). Grain: (grain, period_start) with grain in week|month.
--
--   panel_complete  every seed source has been active since before the period
--   panel_sources   the sources fully active for the period (the footnote)
--   matrix_version  the query-matrix version in force for the period
--   trend_break     the matrix version changed versus the previous period
--   change_1 / change_4 / change_3m_ok
--                   a change metric against the period 1 / 4 / 3 steps back is
--                   allowed only if both periods are panel_complete, share the
--                   same panel and matrix version: otherwise it is NULL, never 0.

WITH bounds AS (

    SELECT
        MIN(first_seen_at) AS lo,
        MAX(first_seen_at) AS hi
    FROM {{ ref('int_market_job') }}

),

periods AS (

    SELECT 'week' AS grain, d::date AS period_start
    FROM bounds,
        GENERATE_SERIES(DATE_TRUNC('week', lo), DATE_TRUNC('week', hi), INTERVAL '1 week') AS d
    UNION ALL
    SELECT 'month' AS grain, d::date AS period_start
    FROM bounds,
        GENERATE_SERIES(DATE_TRUNC('month', lo), DATE_TRUNC('month', hi), INTERVAL '1 month') AS d

),

stamped AS (

    SELECT
        p.grain,
        p.period_start,
        NOT EXISTS (
            SELECT 1 FROM {{ ref('source_panel') }} AS s
            WHERE s.active_from > p.period_start
        ) AS panel_complete,
        (
            SELECT STRING_AGG(s.source_name, ',' ORDER BY s.source_name)
            FROM {{ ref('source_panel') }} AS s
            WHERE s.active_from <= p.period_start
        ) AS panel_sources,
        COALESCE(
            (
                SELECT m.matrix_version
                FROM {{ ref('query_matrix_versions') }} AS m
                WHERE m.effective_from <= p.period_start
                ORDER BY m.effective_from DESC
                LIMIT 1
            ),
            (
                SELECT m.matrix_version
                FROM {{ ref('query_matrix_versions') }} AS m
                ORDER BY m.effective_from
                LIMIT 1
            )
        ) AS matrix_version
    FROM periods AS p

),

lagged AS (

    SELECT
        *,
        LAG(matrix_version, 1) OVER w AS m1,
        LAG(matrix_version, 3) OVER w AS m3,
        LAG(matrix_version, 4) OVER w AS m4,
        LAG(panel_complete, 1) OVER w AS c1,
        LAG(panel_complete, 3) OVER w AS c3,
        LAG(panel_complete, 4) OVER w AS c4,
        LAG(panel_sources, 1) OVER w AS s1,
        LAG(panel_sources, 3) OVER w AS s3,
        LAG(panel_sources, 4) OVER w AS s4
    FROM stamped
    WINDOW w AS (PARTITION BY grain ORDER BY period_start)

)

SELECT
    grain,
    period_start,
    panel_complete,
    panel_sources,
    matrix_version,
    COALESCE(matrix_version IS DISTINCT FROM m1 AND m1 IS NOT NULL, FALSE) AS trend_break,
    COALESCE(panel_complete AND c1 AND panel_sources = s1 AND matrix_version = m1, FALSE) AS change_1_ok,
    COALESCE(panel_complete AND c4 AND panel_sources = s4 AND matrix_version = m4, FALSE) AS change_4_ok,
    COALESCE(panel_complete AND c3 AND panel_sources = s3 AND matrix_version = m3, FALSE) AS change_3_ok
FROM lagged
