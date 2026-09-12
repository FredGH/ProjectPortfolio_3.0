-- dim_job: one row per deduplicated job (PLAN.md Step 11).
-- Grain: job_group_id (unique).
--
-- Field-level survivorship (DECISIONS.md §2.7, §5): description and
-- apply_url/title_for_display are resolved separately by Step 10's
-- core.dedup.survivorship (see silver.job_survivorship, written by the
-- compute-survivorship pipeline CLI subcommand — not dbt, since the
-- source-rank/longest-string rules are already tested there and this
-- avoids a second implementation of the same rules drifting from the
-- first). Every OTHER field (company, location, salary, engagement
-- terms, posted_at) has no survivorship rule stated anywhere in
-- PLAN.md/DECISIONS.md — this model's documented default is that they
-- come from the SAME posting that won apply_url, on the reasoning that
-- it is already established as this job's canonical record (the one a
-- user actually applies through), and Step 10's own title_for_display
-- rule already applies this exact reasoning to one field.

WITH survivorship AS (

    SELECT * FROM {{ source('silver_ingest', 'job_survivorship') }}

),

identity_map AS (

    SELECT * FROM {{ source('silver_ingest', 'job_identity_map') }}

),

-- Category/seniority classification (PLAN.md Step 11a) — written by the
-- classify-jobs pipeline CLI subcommand, not dbt. Joined LEFT below: a
-- job_group_id that hasn't been classified yet (e.g. between
-- cluster-jobs running and classify-jobs catching up) must still appear
-- in dim_job, with these fields NULL.
job_category AS (

    SELECT * FROM {{ source('silver_ingest', 'job_category') }}

),

-- The posting whose apply_url won survivorship — every field not
-- covered by its own explicit rule is taken from here.
apply_posting AS (

    SELECT sp.*
    FROM {{ ref('silver__job_posting') }} AS sp
    INNER JOIN survivorship AS s
        ON sp.source_name = s.apply_source_name
        AND sp.source_job_id = s.apply_source_job_id

),

-- normalised_company/country_iso/region, from the same apply-winning
-- posting's blocking key (Step 7) — exposed here so dim_company and
-- fct_market_demand don't need to re-derive this join chain.
apply_blocking_keys AS (

    SELECT
        s.job_group_id,
        bk.normalised_company,
        bk.country_iso,
        bk.region
    FROM survivorship AS s
    INNER JOIN {{ ref('silver__job_posting') }} AS sp
        ON s.apply_source_name = sp.source_name
        AND s.apply_source_job_id = sp.source_job_id
    INNER JOIN {{ source('dedup_ingest', 'job_blocking_keys') }} AS bk
        ON sp.job_key = bk.job_key

),

-- First-seen timestamp per source posting, from bronze's full
-- (uncollapsed) append-only history — int_jobs__unioned/silver__job_
-- posting only carry the current version, not the first-fetched one.
first_seen AS (

    SELECT
        source_name,
        source_job_id,
        MIN(fetched_at) AS first_seen_at
    FROM {{ source('bronze', 'raw_jobs') }}
    GROUP BY source_name, source_job_id

),

-- Every source URL for a job_group_id, regardless of which one won
-- survivorship — mirrors core.dedup.survivorship.build_sources_array's
-- shape exactly, computed here in SQL since it is pure aggregation
-- with no decision-logic (unlike description/apply_url).
sources AS (

    SELECT
        im.job_group_id,
        jsonb_agg(
            jsonb_build_object(
                'source_name', sp.source_name,
                'job_url', sp.job_url,
                'first_seen_at', fs.first_seen_at
            )
            ORDER BY sp.source_name, sp.source_job_id
        ) AS sources
    FROM identity_map AS im
    INNER JOIN {{ ref('silver__job_posting') }} AS sp
        ON im.source_name = sp.source_name AND im.source_job_id = sp.source_job_id
    LEFT JOIN first_seen AS fs
        ON sp.source_name = fs.source_name AND sp.source_job_id = fs.source_job_id
    GROUP BY im.job_group_id

)

SELECT
    s.job_group_id,
    s.apply_job_url AS apply_url,
    s.apply_title_for_display AS title_for_display,
    s.winning_description AS description,
    ap.title AS title_raw,
    ap.company,
    abk.normalised_company,
    ap.location,
    abk.country_iso,
    abk.region,
    ap.salary_raw,
    ap.rate_currency,
    ap.rate_annualised,
    ap.rate_daily_equivalent,
    ap.contract_length_months,
    ap.engagement_type,
    ap.ir35_status,
    ap.engagement_vehicle,
    ap.rate_basis,
    ap.extension_likelihood,
    ap.posted_at,
    ap.entry_method,
    s.apply_source_name,
    s.apply_source_job_id,
    src.sources,
    jc.category,
    jc.category_confidence,
    jc.category_method,
    jc.qa_category,
    jc.seniority_band
FROM survivorship AS s
INNER JOIN apply_posting AS ap
    ON s.apply_source_name = ap.source_name AND s.apply_source_job_id = ap.source_job_id
INNER JOIN apply_blocking_keys AS abk
    ON s.job_group_id = abk.job_group_id
INNER JOIN sources AS src
    ON s.job_group_id = src.job_group_id
LEFT JOIN job_category AS jc
    ON s.job_group_id = jc.job_group_id
