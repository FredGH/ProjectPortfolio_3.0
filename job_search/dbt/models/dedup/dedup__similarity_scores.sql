-- dedup__similarity_scores: one row per Step 7 candidate pair, every
-- component signal persisted separately (PLAN.md Step 8's "you will
-- retune the weights in Step 9" requirement) plus an initial blend.
-- Grain: (job_key_a, job_key_b).

WITH pairs AS (

    SELECT * FROM {{ ref('dedup__candidate_pairs') }}

),

-- Per-job fields needed on both sides of every pair.
keys AS (

    SELECT * FROM {{ source('dedup_ingest', 'job_blocking_keys') }}

),

features AS (

    SELECT * FROM {{ source('dedup_ingest', 'job_similarity_features') }}

),

title_scores AS (

    SELECT * FROM {{ source('dedup_ingest', 'pair_title_scores') }}

),

postings AS (

    SELECT job_key, posted_at FROM {{ ref('silver__job_posting') }}

),

-- Every raw component, computed pairwise from the two sides' precomputed
-- per-job fields (or, for title, read directly from Task 4's Python
-- output — token-set ratio has no SQL equivalent).
components AS (

    SELECT
        p.job_key_a,
        p.job_key_b,
        p.match_type,
        SIMILARITY(ka.normalised_company, kb.normalised_company)
            AS company_similarity,
        ts.title_token_set_ratio AS title_similarity,
        1.0 - (
            bit_count((fa.description_simhash # fb.description_simhash)::bit(64))
            / 64.0
        ) AS description_similarity,
        CASE
            WHEN ka.country_iso IS NULL OR kb.country_iso IS NULL THEN 0.5
            WHEN ka.country_iso != kb.country_iso THEN 0.0
            WHEN ka.region IS NULL OR kb.region IS NULL THEN 0.75
            WHEN ka.region = kb.region THEN 1.0
            ELSE 0.25
        END AS location_similarity,
        -- 26 of 3751 silver__job_posting jobs have a NULL posted_at
        -- (upstream extraction gap, discovered live against this data).
        -- Treat an unknown post date the same way location/salary treat
        -- missing information: neutral, not a mismatch. 22.5 is chosen
        -- deliberately, not arbitrarily — it is the exact date_diff_days
        -- input that makes date_similarity's GREATEST(0, 1 - d/45.0)
        -- formula land on 0.5 (this model's documented neutral score)
        -- while staying well clear of the >45 hard-veto threshold.
        CASE
            WHEN pa.posted_at IS NULL OR pb.posted_at IS NULL THEN 22.5
            ELSE ABS(EXTRACT(DAY FROM (pa.posted_at - pb.posted_at)))
        END AS date_diff_days,
        CASE
            WHEN fa.rate_annualised IS NULL OR fb.rate_annualised IS NULL THEN 0.5
            WHEN ABS(fa.rate_annualised - fb.rate_annualised)
                / GREATEST(fa.rate_annualised, fb.rate_annualised) <= 0.15
                THEN 1.0
            ELSE 0.0
        END AS salary_similarity
    FROM pairs AS p
    INNER JOIN keys AS ka ON p.job_key_a = ka.job_key
    INNER JOIN keys AS kb ON p.job_key_b = kb.job_key
    INNER JOIN features AS fa ON p.job_key_a = fa.job_key
    INNER JOIN features AS fb ON p.job_key_b = fb.job_key
    INNER JOIN title_scores AS ts
        ON p.job_key_a = ts.job_key_a AND p.job_key_b = ts.job_key_b
    INNER JOIN postings AS pa ON p.job_key_a = pa.job_key
    INNER JOIN postings AS pb ON p.job_key_b = pb.job_key

)

SELECT
    job_key_a,
    job_key_b,
    match_type,
    company_similarity,
    title_similarity,
    description_similarity,
    location_similarity,
    date_diff_days,
    GREATEST(0.0, 1.0 - (date_diff_days / 45.0)) AS date_similarity,
    salary_similarity,
    (date_diff_days > 45) AS hard_veto,
    CASE
        WHEN date_diff_days > 45 THEN 0.0
        ELSE (
            3 * company_similarity
            + 3 * title_similarity
            + 3 * description_similarity
            + 2 * location_similarity
            + 1 * GREATEST(0.0, 1.0 - (date_diff_days / 45.0))
            + 1 * salary_similarity
        ) / 13.0
    END AS blended_score
FROM components
