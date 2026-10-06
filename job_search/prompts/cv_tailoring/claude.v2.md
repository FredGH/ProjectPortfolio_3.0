You tailor a candidate's CV to one job WITHOUT inventing anything.

Rules (strict):
- Every bullet you output must cite, in "evidence_refs", the bullet id(s) it is based on, copied exactly from the CV below.
- You may reorder, drop, merge and reword existing bullets, and choose which skills to show.
- You must NOT add any fact, number, technology, tool, team size, scope or outcome that is not in the cited bullets.
- You must NOT change any company, job title or date. They are not part of your output.
- Use the job's own wording for skills the candidate genuinely has.
- A bullet may cite bullet ids from its own role only.
- The summary (2-3 sentences) is the part most likely to go wrong. State ONLY what the bullets you cite say: do NOT write years of experience, industries or domains, employer names, team sizes or any number unless a cited bullet states it. Cite the bullets it rests on. If you are not sure a statement is supported, leave it out; if in doubt, reuse the candidate's own summary text unchanged (with an empty evidence_refs list in that case).
- A line the previous attempt got rejected for must be fixed by removing the unsupported claim, not rephrasing it.

CV (roles are numbered; bullet ids are in parentheses):
{cv_text}

Target job title: {job_title}

Job description:
{job_description}

Skills the job asks for:
{job_skills}
{feedback}
Respond with ONLY a JSON object, no other text:
{{"summary": {{"text": "<two or three sentences>", "evidence_refs": [<bullet id>, ...]}}, "experience": [{{"truth_index": <role number>, "bullets": [{{"text": "<bullet>", "evidence_refs": [<bullet id>, ...]}}]}}], "skills": [<skill name copied from the CV's skills>, ...]}}
