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
  "publications": [{{"citation": "<full citation text, verbatim>", "authors": ["<author name>", ...], "year": <YYYY or null>}}, ...],
  "education": [{{"institution": "<name>", "grade": "<classification/grade awarded, e.g. Distinction, First Class, 2:1, or null>", "qualification": "<degree/qualification name, without the grade>", "start": "<YYYY or YYYY-MM or null>", "end": "<YYYY or YYYY-MM or null>"}}, ...],
  "qualifications": [{{"name": "<certification or course/programme name>", "year": <YYYY or null>}}, ...],
  "activities": [{{"name": "<activity or role name>", "organisation": "<associated organisation, or null>", "start": "<YYYY or YYYY-MM or null>", "end": "<YYYY or YYYY-MM or null>"}}, ...]
}}

Rules:
- Every bullet's text must be copied verbatim from the source — do not paraphrase, summarize, or invent bullets.
- "email", "phone", "linkedin_url" and "nationality" come from the header block under the person's name, if present.
- "qualifications" covers both formal professional certifications and non-certification training (short courses, programmes) — merge both into this one list.
- "projects" is for personal/side projects, distinct from "experience" (paid roles).
- "authors" is the citation's author list, split into separate names (e.g. "Smith J., Doe A. & Lee K." becomes ["Smith J.", "Doe A.", "Lee K."]). "year" is the publication year if stated in the citation, else null.
- Education dates are always two separate fields. If the source shows a combined range like "2016-2017", split it: "start" gets only the earlier year ("2016"), "end" gets only the later year ("2017") — never put the whole range into "start" and leave "end" null.
- Education "grade" is separate from "qualification": if the source shows something like "MSc in Data Science (Distinction)", "grade" is "Distinction" and "qualification" is "MSc in Data Science" — never leave the grade embedded inside "qualification".
- Each "activities" entry gets its own name/organisation/dates split out from a line like "Mentor at MyJobGlasses (2020-present)": "name" is "Mentor", "organisation" is "MyJobGlasses", "start" is "2020", "end" is null (present/ongoing). A combined range splits the same way as education dates. A single bare year with no range is the start, with "end" left null. A plain interest with no organisation or dates (e.g. "Hiking") still gets its own entry with "name" only.
- If a section is absent from the CV, return an empty list for it (or null, for "summary", "work_auth", "email", "phone", "linkedin_url" and "nationality").
- Respond with ONLY the JSON object, no other text.

CV markdown:
{markdown}
