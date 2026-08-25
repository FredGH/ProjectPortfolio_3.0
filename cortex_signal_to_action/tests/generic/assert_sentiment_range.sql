-- assert_sentiment_range: fails when a sentiment or aspect score falls outside [min_value, max_value].
-- Defaults: min_value=-1.0, max_value=1.0. NULL values are excluded — not a range violation.
-- Returns offending rows — zero rows = test passes.

{% test assert_sentiment_range(model, column_name, min_value=-1.0, max_value=1.0) %}

SELECT {{ column_name }}
FROM {{ model }}
WHERE
    {{ column_name }} IS NOT NULL
    AND ({{ column_name }} < {{ min_value }} OR {{ column_name }} > {{ max_value }})

{% endtest %}
