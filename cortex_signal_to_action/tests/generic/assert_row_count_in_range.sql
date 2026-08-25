-- assert_row_count_in_range: parameterised row count bounds checker.
-- Fails (returns a row) when the model's row count falls outside [min_rows, max_rows].
-- max_rows is optional; omit or set to null to skip the upper bound check.
-- Usage in schema.yml at model level:
--   tests:
--     - assert_row_count_in_range:
--         min_rows: 500
--         max_rows: 800

{% test assert_row_count_in_range(model, min_rows, max_rows=None) %}

SELECT
    COUNT(*)          AS actual_row_count,
    {{ min_rows }}    AS min_expected,
    {{ max_rows if max_rows is not none else 'NULL' }} AS max_expected
FROM {{ model }}
HAVING COUNT(*) < {{ min_rows }}
{% if max_rows is not none %}
    OR COUNT(*) > {{ max_rows }}
{% endif %}

{% endtest %}
