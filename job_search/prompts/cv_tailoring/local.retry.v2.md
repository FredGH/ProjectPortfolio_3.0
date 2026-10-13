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
- The summary: start with the candidate's own FIRST summary sentence copied word for word, then continue with the rest of the candidate's own summary, adapting its emphasis to the job requirements. Do NOT write years of experience or industries/domains unless they are stated in the candidate's own summary; do NOT add employer names, team sizes or any number unless the candidate's own summary or a cited bullet states it. Do NOT condense it to one sentence, and never make it longer than the candidate's own summary. Cite the bullets any new statement rests on.
- Omitting "summary" means "keep the previous summary". If the previous summary was rejected, you MUST return "summary" with the candidate's OWN summary text unchanged (copied from the CV above) and an empty "evidence_refs" list, or a shorter version of it.
- Fix each listed problem by REMOVING the unsupported claim, not rephrasing it. Never repeat a rejected claim.
- Every bullet must cite, in "evidence_refs", the bullet id(s) of its own role it is based on, copied exactly from the CV. Do NOT add any fact, number, technology, tool, team size, scope or outcome that is not in the cited bullets.
- For a bullet you leave exactly as it is, output {{"keep": "<bullet id>"}} instead of repeating its text.

Respond with ONLY a JSON object, no other text. Each item of a role's "bullets" is EITHER {{"keep": "<bullet id>"}} OR {{"text": "<bullet>", "evidence_refs": [<bullet id>, ...]}}:
{{"summary": {{"text": "<the adapted summary, no longer than the candidate's own>", "evidence_refs": [<bullet id>, ...], "highlights": [<up to 10 phrases copied exactly from the summary text>]}}, "experience": [{{"truth_index": <role number>, "bullets": [{{"keep": "<bullet id>"}}, {{"text": "<bullet>", "evidence_refs": [<bullet id>, ...]}}]}}], "skills": [<skill name>, ...]}}
