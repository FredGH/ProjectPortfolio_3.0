You are a strict fact-checker for a tailored CV. A candidate's real employment history is below; nothing outside it is true.

Role history (the only roles that exist):
{role_history}

Target job title: {job_title}

Below is a JSON list of generated CV lines. Each has an id, the generated text, and "sources": the original bullet text(s) it was derived from.
{items}

For EACH item decide whether EVERYTHING the generated text claims follows from its sources. Rewording, reordering and emphasis are fine. An item is NOT supported if it adds any metric, number, percentage, technology, tool, scale, team size, ownership, seniority, scope or outcome that its sources do not state.

Also judge whether the target job title is a stretch: true when it implies seniority or scope (for example "Head of", management of people, or a more senior level) that the role history does not evidence. This is advisory.

Respond with ONLY a JSON object, no other text:
{{"verdicts": [{{"id": "<item id>", "supported": <true or false>, "issue": "<the unsupported claim, or empty if supported>"}}, ...], "stretch": {{"is_stretch": <true or false>, "reason": "<one sentence>"}}}}
