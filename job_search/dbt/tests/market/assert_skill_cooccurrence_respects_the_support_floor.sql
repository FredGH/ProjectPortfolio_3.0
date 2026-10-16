-- Pairs below the support floor must be dropped, pairs are ordered, and lift is
-- positive. Passes on zero rows.

SELECT *
FROM {{ ref('fct_skill_cooccurrence') }}
WHERE support < 3 OR skill_a >= skill_b OR lift <= 0
