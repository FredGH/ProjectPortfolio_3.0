You assess how well a candidate's CV fits a job description.

CV:
{cv_text}

Job description:
{jd_text}

Rate the fit from 0 to 100, explain briefly, list any specific skills the job
asks for that the CV does not show, and flag whether this role is a stretch
(more senior or different in kind from the CV's demonstrated experience).

Respond with ONLY a JSON object, no other text:
{{"fit_score": <0-100>, "rationale": "<one or two sentences>", "missing_skills": [<string>, ...], "stretch_flag": <true or false>}}
