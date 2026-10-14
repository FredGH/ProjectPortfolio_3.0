You help a candidate decide how to position themselves for one job. Using ONLY the job description and the company profile below, write a short analysis. This is analysis, not fact: do not invent figures, events or capabilities of the company or its competitors.

Job: {title} at {company}

Job description:
{description}

Company profile (JSON; every fact cites its source):
{profile}

Listed competitors: {competitors}

Write:
- "transformation": 2-3 sentences on what change or transformation the job description implies for the company (what they are building, replacing or scaling).
- "advantages": up to 5 items. Each says how that change could put the company ahead of ONE listed competitor (put its exact name in "competitor") or of the field in general ("competitor": ""). Each "point" is one sentence grounded in the description or the profile; if you cannot ground a point, leave it out.
- "caveats": 1-3 short notes on what is uncertain.

Respond with ONLY a JSON object, no other text:
{{"transformation": "...", "advantages": [{{"competitor": "...", "point": "..."}}], "caveats": ["..."]}}
