-- assert_kl_divergence: Tier 4 distributional drift detection.
-- Approximates KL divergence between the observed categorical column distribution
-- and a uniform reference (1/k probability per category, where k = distinct category count).
--
-- KL(P || Q_uniform) = SUM(p_i * LN(k * p_i)) for p_i > 0
--
-- A KL divergence of 0 means the observed distribution is perfectly uniform.
-- Higher values indicate increasing concentration into fewer categories.
-- For a 5-category column (e.g. sentiment_label), a uniform distribution yields KL = 0;
-- full concentration into one category yields KL ≈ LN(5) ≈ 1.61.
--
-- Parameters:
--   max_kl_divergence — upper bound before the test fails (default: 0.5)
--
-- Usage in schema.yml:
--   tests:
--     - assert_kl_divergence:
--         max_kl_divergence: 0.5
--         severity: warn

{% test assert_kl_divergence(model, column_name, max_kl_divergence=0.5) %}

WITH category_counts AS (
    SELECT
        {{ column_name }} AS category,
        COUNT(*) AS cnt
    FROM {{ model }}
    WHERE {{ column_name }} IS NOT NULL
    GROUP BY {{ column_name }}
),

totals AS (
    SELECT
        SUM(cnt) AS total_rows,
        COUNT(*) AS k
    FROM category_counts
),

probabilities AS (
    SELECT
        c.category,
        c.cnt::FLOAT / t.total_rows AS p_i,
        t.k AS k
    FROM category_counts AS c
    CROSS JOIN totals AS t
),

kl_terms AS (
    SELECT
        category,
        p_i,
        k,
        -- KL(P || Uniform) term: p_i * ln(k * p_i)
        p_i * LN(k * p_i) AS kl_term
    FROM probabilities
    WHERE p_i > 0
),

kl_summary AS (
    SELECT
        SUM(kl_term) AS kl_divergence,
        SUM(p_i) AS probability_sum,
        MAX(k) AS num_categories,
        COUNT(*) AS categories_with_data
    FROM kl_terms
)

SELECT
    '{{ column_name }}'::VARCHAR AS column_name,
    ROUND(kl_divergence::FLOAT, 6) AS kl_divergence,
    {{ max_kl_divergence }}::FLOAT AS max_kl_divergence,
    num_categories,
    categories_with_data
FROM kl_summary
WHERE kl_divergence > {{ max_kl_divergence }}

{% endtest %}
