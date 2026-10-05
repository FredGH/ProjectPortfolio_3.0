You are fixing a tailored CV. Your previous output for this job was checked and some lines were rejected.

CV (roles are numbered; bullet ids are in parentheses):
{cv_text}

Target job title: {job_title}

Skills the job asks for:
{job_skills}

Your previous output (JSON):
{previous_output}
{feedback}
Return a JSON patch in the same format, containing ONLY the parts that must change:
- Omit "summary" if it is fine.
- Omit every role whose bullets are fine; an omitted role stays exactly as it was. Include a role only to replace its whole bullet list.
- Omit "skills" if fine.
- The summary (2-3 sentences) is the part most likely to go wrong. Do NOT write years of experience, industries or domains, employer names, team sizes or any number unless a cited bullet states it. Cite the bullets it rests on.
- Omitting "summary" means "keep the previous summary". If the previous summary was rejected, you MUST return "summary" with the candidate's OWN summary text unchanged (copied from the CV above) and an empty "evidence_refs" list, or a shorter version that states only what its cited bullets say.
- Fix each listed problem by REMOVING the unsupported claim, not rephrasing it. Never repeat a rejected claim.
- Every bullet must cite, in "evidence_refs", the bullet id(s) of its own role it is based on, copied exactly from the CV. Do NOT add any fact, number, technology, tool, team size, scope or outcome that is not in the cited bullets.
- For a bullet you leave exactly as it is, output {{"keep": "<bullet id>"}} instead of repeating its text.

Respond with ONLY a JSON object, no other text. Each item of a role's "bullets" is EITHER {{"keep": "<bullet id>"}} OR {{"text": "<bullet>", "evidence_refs": [<bullet id>, ...]}}:
{{"summary": {{"text": "<two or three sentences>", "evidence_refs": [<bullet id>, ...]}}, "experience": [{{"truth_index": <role number>, "bullets": [{{"keep": "<bullet id>"}}, {{"text": "<bullet>", "evidence_refs": [<bullet id>, ...]}}]}}], "skills": [<skill name>, ...]}}
