Extract structured CV data from the markdown below into JSON matching exactly this shape:

{{
  "identity": "<full name>",
  "headline": "<professional headline, e.g. current or most recent job title>",
  "email": "<email address, or null if not stated>",
  "phone": "<phone number, or null if not stated>",
  "linkedin_url": "<LinkedIn profile URL, or null if not stated>",
  "nationality": "<nationality, or null if not stated>",
  "summary": "<professional summary/profile paragraph, verbatim, or null if not stated>",
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
  "projects": [{{"name": "<project name>", "description": "<what it does, verbatim>", "tech": ["<technology mentioned>", ...], "url": "<link, or null>"}}, ...],
  "publications": [{{"citation": "<full citation text>"}}, ...],
  "education": [{{"institution": "<name>", "qualification": "<degree/qualification>", "start": "<YYYY or YYYY-MM or null>", "end": "<YYYY or YYYY-MM or null>"}}, ...],
  "qualifications": [{{"name": "<certification or course/programme name>", "year": <YYYY or null>}}, ...],
  "activities_interests": ["<activity or interest, verbatim>", ...]
}}

Rules:
- Every bullet's text must be copied verbatim from the source — do not paraphrase, summarize, or invent bullets.
- "email", "phone", "linkedin_url" and "nationality" come from the header block under the person's name, if present.
- "qualifications" covers both formal professional certifications and non-certification training (short courses, programmes) — merge both into this one list.
- "projects" is for personal/side projects, distinct from "experience" (paid roles).
- If a section is absent from the CV, return an empty list for it (or null, for "summary", "work_auth", "email", "phone", "linkedin_url" and "nationality").
- Respond with ONLY the JSON object, no other text.

CV markdown:
{markdown}
