-- silver__skill: the unified skill vocabulary — every ESCO skill plus every
-- custom skill (tools ESCO lacks, from the seed file or the review page).
-- Grain: skill_id (unique).

SELECT
    skill_id,
    preferred_label AS canonical_label,
    'esco' AS source
FROM {{ source('esco_ingest', 'skill') }}

UNION ALL

SELECT
    skill_id,
    canonical_label,
    'custom' AS source
FROM {{ source('silver_ingest', 'custom_skill') }}
