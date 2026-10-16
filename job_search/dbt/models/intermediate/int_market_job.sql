-- int_market_job: the shared base of the Step 21a market marts.
-- Grain: job_group_id (unique). One row per deduplicated API job that at
-- least one PANEL source has delivered (seeds/source_panel.csv). Manual
-- entries (selection bias) and the test_source_* rows are excluded here, once,
-- so no mart can leak them.
--
-- first_seen_at is when WE first saw the job through a panel source, not when
-- the employer posted it: the stable source panel protects exactly that
-- (PLAN.md Step 21a). posted_at is kept for age.

WITH panel AS (

    SELECT source_name FROM {{ ref('source_panel') }}

),

postings AS (

    SELECT
        im.job_group_id,
        im.source_name,
        im.source_job_id,
        MIN(r.fetched_at) AS first_fetched,
        MAX(r.fetched_at) AS last_fetched
    FROM {{ source('silver_ingest', 'job_identity_map') }} AS im
    INNER JOIN panel AS p
        ON im.source_name = p.source_name
    INNER JOIN {{ source('bronze', 'raw_jobs') }} AS r
        ON im.source_name = r.source_name AND im.source_job_id = r.source_job_id
    GROUP BY im.job_group_id, im.source_name, im.source_job_id

),

per_job AS (

    SELECT
        job_group_id,
        MIN(first_fetched) AS first_fetched,
        MAX(last_fetched) AS last_fetched,
        -- A later duplicate first seen more than 14 days after the earliest is a
        -- repost; anything closer is the same posting syndicated.
        (MAX(first_fetched) - MIN(first_fetched)) > INTERVAL '14 days' AS is_reposted,
        ARRAY_AGG(DISTINCT source_name ORDER BY source_name) AS panel_sources,
        (ARRAY_AGG(source_name ORDER BY first_fetched, source_name))[1] AS first_source
    FROM postings
    GROUP BY job_group_id

),

agency_resolution AS (

    SELECT job_group_id, poster_kind
    FROM {{ source('intel_ingest', 'job_company') }}

)

SELECT
    dj.job_group_id,
    COALESCE(pj.first_fetched, dj.posted_at) AS first_seen_at,
    DATE_TRUNC('week', COALESCE(pj.first_fetched, dj.posted_at))::date AS first_seen_week,
    DATE_TRUNC('month', COALESCE(pj.first_fetched, dj.posted_at))::date AS first_seen_month,
    pj.last_fetched AS last_seen_at,
    dj.posted_at,
    pj.panel_sources,
    pj.is_reposted,
    pj.first_source,
    COALESCE(dj.category, 'unclassified') AS category,
    COALESCE(dj.seniority_band, 'unknown') AS seniority_band,
    COALESCE(dj.region, 'unknown') AS region,
    dj.normalised_company,
    dj.company,
    CASE dj.engagement_type
        WHEN 'permanent' THEN 'permanent'
        WHEN 'contract' THEN 'contract'
        WHEN 'interim' THEN 'contract'
        WHEN 'ftc' THEN 'contract'
        ELSE 'unknown'
    END AS contract_type,
    -- Salary is GBP only (a missing currency is a UK source's default); other
    -- currencies are excluded rather than mixed into a GBP median.
    CASE
        WHEN dj.engagement_type = 'permanent'
            AND (dj.rate_currency IS NULL OR dj.rate_currency = 'GBP')
            THEN dj.rate_annualised
    END AS annual_salary,
    CASE
        WHEN dj.engagement_type IN ('contract', 'interim', 'ftc')
            AND (dj.rate_currency IS NULL OR dj.rate_currency = 'GBP')
            THEN dj.rate_daily_equivalent
    END AS day_rate,
    COALESCE(ar.poster_kind = 'agency', {{ agency_name_matches('dj.company') }}) AS is_agency,
    {{ work_model("COALESCE(dj.title_raw, '') || ' ' || COALESCE(dj.location, '') || ' ' || COALESCE(dj.description, '')") }} AS work_model
FROM {{ ref('dim_job') }} AS dj
INNER JOIN per_job AS pj
    ON dj.job_group_id = pj.job_group_id
LEFT JOIN agency_resolution AS ar
    ON dj.job_group_id = ar.job_group_id
WHERE dj.entry_method = 'api'
