-- assert_dim_job_covers_every_job_group: dim_job's chain of INNER JOINs
-- (survivorship -> apply_posting -> apply_blocking_keys -> sources)
-- means a job_group_id whose compute-survivorship/compute-blocking-keys
-- rows are stale (out-of-band CLI steps not rerun since new postings
-- were clustered) silently vanishes from dim_job rather than erroring
-- — the same hazard dedup__similarity_scores has one layer down (see
-- assert_similarity_scores_match_candidate_pairs_count). A test passes
-- on zero rows returned.

SELECT im.job_group_id
FROM {{ source('silver_ingest', 'job_identity_map') }} AS im
LEFT JOIN {{ ref('dim_job') }} AS dj USING (job_group_id)
WHERE dj.job_group_id IS NULL
GROUP BY im.job_group_id
