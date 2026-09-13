Extract structured CV data from the markdown below into JSON matching exactly this shape:

{{
  "identity": "<full name>",
  "headline": "<professional headline, e.g. current or most recent job title>",
  "locations": ["<city, country>", ...],
  "work_auth": "<work authorization statement, or null if not stated>",
  "skills": [{{"name": "<skill name>", "years": <number or null>, "last_used": "<YYYY-MM or null>"}}, ...],
  "experience": [
    {{
      "company": "<employer name>",
      "title": "<job title>",
      "start": "<YYYY-MM>",
      "end": "<YYYY-MM or null if current>",
      "bullets": ["<bullet text, verbatim>", ...],
      "tech": ["<technology mentioned>", ...],
      "metrics": ["<quantified achievement mentioned>", ...]
    }},
    ...
  ],
  "education": [{{"institution": "<name>", "qualification": "<degree/qualification>", "start": "<YYYY or YYYY-MM or null>", "end": "<YYYY or YYYY-MM or null>"}}, ...],
  "certifications": [{{"name": "<certification name>", "year": <YYYY or null>}}, ...],
  "publications": [{{"citation": "<full citation text>"}}, ...]
}}

Rules:
- Every bullet's text must be copied verbatim from the source — do not paraphrase, summarize, or invent bullets.
- If a section is absent from the CV, return an empty list for it.
- Respond with ONLY the JSON object, no other text.

CV markdown:
{markdown}
