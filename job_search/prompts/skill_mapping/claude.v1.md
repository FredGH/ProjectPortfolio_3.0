You match skill names taken from job descriptions to entries in the ESCO skills taxonomy.

For each numbered skill below, decide which of its candidate ESCO skills (if any) means the SAME skill. Be strict:
- "match": one candidate is the same skill, or a standard alternative name for it. Give its candidate number and a confidence: "high" only if you are sure they are the same skill; otherwise "low". A related-but-broader or narrower skill is NOT a match (e.g. "GPU" is not "GPU programming").
- "no_equivalent": none of the candidates is the same skill. Very common for modern tools, frameworks and techniques that ESCO predates. If the skill is real and specific, give a short canonical "custom_label" for a custom skill.
- "unsure": you cannot tell what the skill is or cannot decide.

Skills:
{strings}

Respond with ONLY a JSON object, no other text:
{{"results": [{{"n": <skill number>, "verdict": "match" | "no_equivalent" | "unsure", "candidate": <candidate number or null>, "confidence": "high" | "low" | null, "custom_label": <string or null>, "note": "<one short reason>"}}]}}
Include exactly one entry for every numbered skill.
