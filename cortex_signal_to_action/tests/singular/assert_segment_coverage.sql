-- assert_segment_coverage: every customer in slv_customer_profile must have a segment
-- assignment in mrt_customer_segments. Any returned row is an orphaned customer — a failure.

SELECT p.customer_unique_id
FROM {{ ref('slv_customer_profile') }} AS p
LEFT JOIN {{ ref('mrt_customer_segments') }} AS s USING (customer_unique_id)
WHERE s.customer_unique_id IS NULL
