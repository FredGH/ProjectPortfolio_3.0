-- The AI-term regexes must reproduce every example in seeds/ai_term_cases.csv,
-- including the ordinary-word look-alikes (RAG status, MCP the certification,
-- performance fine-tuning) they must NOT match. Passes on zero rows.

SELECT c.text, c.term, c.expected, (c.text ~* t.pattern) AS matched
FROM {{ ref('ai_term_cases') }} AS c
INNER JOIN {{ ref('ai_terms') }} AS t USING (term)
WHERE (c.text ~* t.pattern) <> (c.expected = 'true')
