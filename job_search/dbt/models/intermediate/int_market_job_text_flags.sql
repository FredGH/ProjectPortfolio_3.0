-- int_market_job_text_flags: per targeted-corpus job, which AI terms its
-- description mentions and whether it uses oversight or build wording
-- (PLAN.md Step 21b macro marts). Grain: job_group_id (unique). Flags only:
-- descriptions are not copied into every mart.

SELECT
    j.job_group_id,
    j.category,
    j.first_seen_month,
    COALESCE(
        ARRAY(
            SELECT t.term FROM {{ ref('ai_terms') }} AS t
            WHERE d.description ~* t.pattern
            ORDER BY t.term
        ),
        ARRAY[]::text[]
    ) AS ai_terms,
    EXISTS (
        SELECT 1 FROM {{ ref('displacement_phrases') }} AS p
        WHERE p.kind = 'oversight' AND d.description ~* p.pattern
    ) AS mentions_oversight,
    EXISTS (
        SELECT 1 FROM {{ ref('displacement_phrases') }} AS p
        WHERE p.kind = 'build' AND d.description ~* p.pattern
    ) AS mentions_build
FROM {{ ref('int_market_job') }} AS j
INNER JOIN {{ ref('dim_job') }} AS d
    ON j.job_group_id = d.job_group_id
