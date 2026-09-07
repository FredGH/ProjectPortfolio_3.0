-- dedup__similarity_scores must have exactly one row per dedup__candidate_pairs
-- row. A row-count mismatch means the out-of-band pair_title_scores/
-- job_similarity_features tables (written by pipeline CLI subcommands, not
-- dbt) are stale relative to a freshly-rebuilt dedup__candidate_pairs — the
-- INNER JOINs in dedup__similarity_scores.sql silently drop unmatched rows
-- rather than erroring, so this is the test that catches that silently.
-- Passes when it returns zero rows (per this project's singular-test
-- convention); a returned row names which side has more rows and by how much.

WITH counts AS (

    SELECT
        (SELECT COUNT(*) FROM {{ ref('dedup__candidate_pairs') }}) AS candidate_pairs_count,
        (SELECT COUNT(*) FROM {{ ref('dedup__similarity_scores') }}) AS similarity_scores_count

)

SELECT
    candidate_pairs_count,
    similarity_scores_count
FROM counts
WHERE candidate_pairs_count != similarity_scores_count
