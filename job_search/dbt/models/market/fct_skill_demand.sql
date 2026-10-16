-- fct_skill_demand: category x region x month x skill (PLAN.md Step 21a).
-- Grain: (period_start, category, region, skill_id). rank_delta_90d is the
-- rising/falling signal (positive = rising): NULL until a comparable period
-- 3 months back exists on the same complete panel and matrix version.

WITH category_postings AS (

    SELECT
        first_seen_month AS period_start,
        category,
        region,
        COUNT(*) AS category_postings
    FROM {{ ref('int_market_job') }}
    GROUP BY 1, 2, 3

),

demand AS (

    SELECT
        s.first_seen_month AS period_start,
        s.category,
        s.region,
        s.skill_id,
        COUNT(DISTINCT s.job_group_id) AS frequency,
        COUNT(*) FILTER (WHERE s.requirement_level = 'must_have') AS must_have_postings,
        COUNT(*) FILTER (WHERE s.requirement_level = 'nice_to_have') AS nice_to_have_postings
    FROM {{ ref('int_market_job_skill') }} AS s
    GROUP BY 1, 2, 3, 4

),

ranked AS (

    SELECT
        d.*,
        cp.category_postings,
        RANK() OVER (
            PARTITION BY d.period_start, d.category, d.region
            ORDER BY d.frequency DESC
        ) AS skill_rank
    FROM demand AS d
    INNER JOIN category_postings AS cp
        USING (period_start, category, region)

)

SELECT
    r.period_start,
    r.category,
    r.region,
    r.skill_id,
    sk.canonical_label AS skill_label,
    r.frequency,
    r.category_postings,
    r.frequency::numeric / r.category_postings AS share_of_category_postings,
    r.skill_rank,
    CASE WHEN st.change_3_ok AND p.skill_rank IS NOT NULL
        THEN p.skill_rank - r.skill_rank END AS rank_delta_90d,
    r.must_have_postings,
    r.nice_to_have_postings,
    CASE WHEN r.must_have_postings + r.nice_to_have_postings > 0
        THEN r.must_have_postings::numeric / (r.must_have_postings + r.nice_to_have_postings)
    END AS must_have_ratio,
    st.panel_complete,
    st.panel_sources,
    st.matrix_version,
    st.trend_break
FROM ranked AS r
INNER JOIN {{ ref('int_market_period_stamp') }} AS st
    ON st.grain = 'month' AND st.period_start = r.period_start
LEFT JOIN ranked AS p
    ON p.period_start = (r.period_start - INTERVAL '3 months')::date
    AND p.category = r.category AND p.region = r.region AND p.skill_id = r.skill_id
LEFT JOIN {{ ref('silver__skill') }} AS sk
    ON r.skill_id = sk.skill_id
