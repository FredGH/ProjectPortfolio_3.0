-- silver__bridge_job_skill: which normalised skills each deduplicated job asks
-- for, and whether each is a must-have or a nice-to-have (PLAN.md Step 14).
-- Grain: (job_group_id, skill_id) (unique).
--
-- Unmapped strings are excluded on purpose: they live in the review list
-- (silver.skill_mapping.review_status), not in this mart.

WITH latest_extraction AS (

    -- Each job's most recent completed extraction run, so a re-extraction
    -- under a new prompt version replaces the old one instead of mixing in.
    SELECT DISTINCT ON (job_group_id)
        job_group_id,
        prompt_version
    FROM {{ source('silver_ingest', 'job_skill_extraction') }}
    ORDER BY job_group_id ASC, extracted_at DESC

),

job_skills AS (

    -- The raw skill strings from that latest run.
    SELECT
        r.job_group_id,
        r.raw_norm,
        r.requirement_level
    FROM {{ source('silver_ingest', 'job_skill_raw') }} AS r
    INNER JOIN latest_extraction AS le
        ON r.job_group_id = le.job_group_id
        AND r.prompt_version = le.prompt_version

),

mapped AS (

    -- Only strings that resolved to a skill.
    SELECT
        js.job_group_id,
        m.skill_id,
        js.requirement_level
    FROM job_skills AS js
    INNER JOIN {{ source('silver_ingest', 'skill_mapping') }} AS m
        ON js.raw_norm = m.raw_norm
    WHERE m.skill_id IS NOT NULL

)

SELECT
    job_group_id,
    skill_id,
    -- A skill named as required anywhere in the job is required.
    CASE
        WHEN BOOL_OR(requirement_level = 'must_have') THEN 'must_have'
        ELSE 'nice_to_have'
    END AS requirement_level,
    COUNT(*) AS mention_count
FROM mapped
GROUP BY job_group_id, skill_id
