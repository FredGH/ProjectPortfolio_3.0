-- The Jensen-Shannon macro behind fct_role_drift: identical distributions give
-- 0, disjoint ones give ln 2, it is symmetric and bounded by ln 2. Passes on
-- zero rows.

WITH cases AS (

    SELECT *
    FROM (VALUES
        ('identical', 1, 0.5, 0.5), ('identical', 1, 0.5, 0.5),
        ('disjoint', 2, 1.0, 0.0), ('disjoint', 2, 0.0, 1.0),
        ('overlap_ab', 3, 0.7, 0.2), ('overlap_ab', 3, 0.3, 0.8),
        ('overlap_ba', 4, 0.2, 0.7), ('overlap_ba', 4, 0.8, 0.3)
    ) AS t (name, grp, p, q)

),

js AS (

    SELECT name, grp, {{ js_divergence('p::numeric', 'q::numeric') }} AS value
    FROM cases
    GROUP BY name, grp

)

SELECT 'identical is not 0' AS problem FROM js WHERE name = 'identical' AND ABS(value) > 1e-9
UNION ALL
SELECT 'disjoint is not ln 2' FROM js WHERE name = 'disjoint' AND ABS(value - LN(2)) > 1e-9
UNION ALL
SELECT 'not symmetric'
FROM (SELECT MAX(value) FILTER (WHERE name = 'overlap_ab') AS a,
             MAX(value) FILTER (WHERE name = 'overlap_ba') AS b FROM js) AS x
WHERE ABS(a - b) > 1e-9
UNION ALL
SELECT 'out of bounds' FROM js WHERE value < -1e-9 OR value > LN(2) + 1e-9
