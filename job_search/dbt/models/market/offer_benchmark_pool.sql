-- offer_benchmark_pool: the postings an offer is compared against (PLAN.md
-- Step 21c). Grain: job_group_id (unique). Contract and permanent postings of
-- the targeted corpus; figure is NULL when the posting did not state pay in the
-- right basis (contract: a stated day or hourly rate, GBP per day; permanent: a
-- stated annual salary, GBP per year). Undisclosed postings stay in on purpose:
-- the disclosure rate of a slice is part of every answer.

SELECT
    j.job_group_id,
    d.title_for_display AS title,
    j.company,
    j.category,
    j.region,
    j.seniority_band,
    j.contract_type,
    COALESCE(d.ir35_status, 'undetermined') AS ir35_status,
    CASE j.contract_type
        WHEN 'permanent' THEN 'GBP per year'
        ELSE 'GBP per day'
    END AS unit,
    CASE j.contract_type
        WHEN 'permanent' THEN j.annual_salary
        ELSE j.day_rate
    END AS figure,
    j.first_seen_month
FROM {{ ref('int_market_job') }} AS j
INNER JOIN {{ ref('dim_job') }} AS d
    ON j.job_group_id = d.job_group_id
WHERE j.contract_type IN ('permanent', 'contract')
