{#
  cortex_sentiment: null-safe wrapper around SNOWFLAKE.CORTEX.AI_SENTIMENT.

  Returns the raw VARIANT from AI_SENTIMENT, or NULL if the input is NULL or blank.
  Empty-string guard prevents Cortex errors on zero-length review text.

  Args:
      text_col:  Column expression containing the text to score.
      aspects:   SQL ARRAY expression of aspect strings, e.g.
                 ARRAY_CONSTRUCT('product_quality', 'delivery').
                 Defaults to an empty array (overall sentiment only).
#}
{% macro cortex_sentiment(text_col, aspects="ARRAY_CONSTRUCT()") %}
    CASE
        WHEN {{ text_col }} IS NULL OR TRIM({{ text_col }}) = ''
            THEN NULL
        ELSE AI_SENTIMENT(
            {{ text_col }},
            {{ aspects }}
        )
    END
{% endmacro %}
