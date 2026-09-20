Extract every skill, technology, tool or method a candidate is expected to have from the job description below, and mark each as required or optional.

Respond with ONLY a JSON object of exactly this shape:

{{
  "skills": [
    {{"skill": "<skill name, e.g. Python>", "requirement_level": "must_have"}},
    {{"skill": "<skill name, e.g. Terraform>", "requirement_level": "nice_to_have"}}
  ]
}}

Rules:
- "requirement_level" is "nice_to_have" ONLY when the text explicitly hedges the skill: "nice to have", "bonus", "preferred", "a plus", "desirable", "advantageous", "would be an asset", "ideally", or the skill sits under a heading such as "Nice to have" or "Bonus points".
- Every other skill that is required, expected, used in the role or its responsibilities, or listed under requirements is "must_have" — including skills stated with no hedging language at all.
- Name each skill once, using its shortest common name (e.g. "Kubernetes", not "experience with Kubernetes clusters"). Do not put years of experience, seniority words or level adjectives in the name.
- Include specific technologies and tools (e.g. "Terraform", "PostgreSQL") and named methods or domains (e.g. "data modelling", "CI/CD"). Do not include soft skills, benefits, company values or job titles.
- If the text names no skills, return {{"skills": []}}.
- Do not invent skills the text does not mention.

Job description:
{description}
