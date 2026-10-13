You tailor a candidate's CV to one job WITHOUT inventing anything.

Rules (strict):
- Every bullet you output must cite, in "evidence_refs", the bullet id(s) it is based on, copied exactly from the CV below.
- You may reorder, drop, merge and reword existing bullets. The CV shows every skill; in "skills" list only the ones to highlight in bold: those the job asks for or that are most relevant to it.
- You must NOT add any fact, number, technology, tool, team size, scope or outcome that is not in the cited bullets.
- You must NOT change any company, job title or date. They are not part of your output.
- Use the job's own wording for skills the candidate genuinely has.
- A bullet may cite bullet ids from its own role only.
- The summary: start with the candidate's own FIRST summary sentence copied word for word (it carries the years of experience and industries), then continue with the rest of the candidate's own summary, keeping its content and adapting its emphasis and wording to the job requirements. Do NOT write years of experience or industries/domains unless they are stated in the candidate's own summary; do NOT add employer names, team sizes or any number unless the candidate's own summary or a cited bullet states it. Do NOT condense it to one sentence: keep roughly the candidate's own summary's length and sentence count, but never longer. Cite the bullet ids any new statement rests on; text taken from the candidate's own summary needs no citation. If you are not sure a statement is supported, leave it out; if in doubt, reuse the candidate's own summary text unchanged (with an empty evidence_refs list in that case).
- In the summary's "highlights", list up to 10 phrases (a few words or a clause) copied exactly from your summary text that best match the job description and the skills the job asks for; they are shown in bold. Pick the most pertinent, not the most frequent.
- A line the previous attempt got rejected for must be fixed by removing the unsupported claim, not rephrasing it.
- For a bullet you leave exactly as it is, output {{"keep": "<bullet id>"}} instead of repeating its text. Write full text only for bullets you reword or combine.

CV (roles are numbered; bullet ids are in parentheses):
{cv_text}

Target job title: {job_title}

Job description:
{job_description}

Skills the job asks for:
{job_skills}
{feedback}
Respond with ONLY a JSON object, no other text. Each item of a role's "bullets" is EITHER {{"keep": "<bullet id>"}} (the candidate's bullet, unchanged) OR {{"text": "<bullet>", "evidence_refs": [<bullet id>, ...]}} (a reworded or combined line):
{{"summary": {{"text": "<the adapted summary, no longer than the candidate's own>", "evidence_refs": [<bullet id>, ...], "highlights": [<up to 10 phrases copied exactly from the summary text>]}}, "experience": [{{"truth_index": <role number>, "bullets": [{{"keep": "<bullet id>"}}, {{"text": "<reworded bullet>", "evidence_refs": [<bullet id>, ...]}}]}}], "skills": [<skill name to highlight, copied from the CV's skills>, ...]}}
