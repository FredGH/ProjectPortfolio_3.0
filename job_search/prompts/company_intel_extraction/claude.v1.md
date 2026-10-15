You build a short, factual company profile from the pages below. Use ONLY the text of the pages for facts.

Company: {company}

{previous}

Pages (each starts with its URL):
{pages}

Rules:
- Every item in the fact lists must cite, in "source_url", the exact URL of the page its text comes from. If no page supports a statement, leave it out. An empty list is fine.
- Do not add numbers, dates, funding figures or names that are not in the pages. Keep each "text" to one or two sentences.
- "business_model": what the company sells and to whom. "size_signals": headcount, offices, growth wording. "tech_stack": technologies the pages mention. "recent_announcements": news the pages date or describe as recent (include the date if the page gives one). "notes": anything else useful for a cover letter.
- "competitors": up to 5 companies that compete with {company}. Use basis "site" (and cite the page in "source_url") only if the pages name or clearly imply the competitor. Otherwise use basis "model_knowledge" with "source_url": null, from your general knowledge - only well-known real companies you are confident compete in the same market. Fewer than 5 is fine. "why" is one sentence.

Respond with ONLY a JSON object, no other text:
{{"business_model": [{{"text": "...", "source_url": "..."}}], "size_signals": [], "tech_stack": [], "recent_announcements": [], "notes": [], "competitors": [{{"name": "...", "why": "...", "basis": "site|model_knowledge", "source_url": "<url or null>"}}]}}
