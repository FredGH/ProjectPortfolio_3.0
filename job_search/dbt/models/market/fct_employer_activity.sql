-- fct_employer_activity: company x category x month (PLAN.md Step 21a).
-- Grain: (period_start, normalised_company, category). Volume, agency vs
-- direct, and a new-entrant flag. is_new_entrant is NULL (unknown) until the
-- month has a comparable earlier period on the same complete panel: every
-- company is "new" in the first month of collection. first_seen_month also
-- makes this the seeding source for the Step 4 target-company registry.

WITH monthly AS (

    SELECT
        first_seen_month AS period_start,
        normalised_company,
        MAX(company) AS company_name,
        category,
        COUNT(*) AS postings,
        COUNT(*) FILTER (WHERE is_agency) AS agency_postings,
        COUNT(*) FILTER (WHERE NOT is_agency) AS direct_postings
    FROM {{ ref('int_market_job') }}
    WHERE normalised_company IS NOT NULL
    GROUP BY 1, 2, 4

),

first_month AS (

    SELECT normalised_company, MIN(first_seen_month) AS first_seen_month
    FROM {{ ref('int_market_job') }}
    WHERE normalised_company IS NOT NULL
    GROUP BY normalised_company

)

SELECT
    m.period_start,
    m.normalised_company,
    m.company_name,
    m.category,
    m.postings,
    m.agency_postings,
    m.direct_postings,
    m.agency_postings::numeric / m.postings AS agency_share,
    fm.first_seen_month,
    CASE WHEN st.change_1_ok THEN m.period_start = fm.first_seen_month END AS is_new_entrant,
    st.panel_complete,
    st.panel_sources,
    st.matrix_version,
    st.trend_break
FROM monthly AS m
INNER JOIN first_month AS fm USING (normalised_company)
INNER JOIN {{ ref('int_market_period_stamp') }} AS st
    ON st.grain = 'month' AND st.period_start = m.period_start
