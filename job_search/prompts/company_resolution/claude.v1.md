You read one job posting and decide which company the work is really for.

Posting:
- Title: {title}
- Posted by (company field): {company}
- Location: {location}
- Seen at: {urls}

Description:
{description}

Decide:
- "poster_kind": "employer" if the company that posted it is the one that would employ the person; "agency" if it is a recruitment agency, staffing firm or consultancy placing someone with a client; "unknown" if you cannot tell.
- "end_client": for an agency, the client's name ONLY if the description names it explicitly (never guess from phrases like "a leading bank"); otherwise null. For an employer, null.
- "website_url": the official website (https://...) of the company the work is for - the employer, or the end client - only if you are confident of it; otherwise null. Never invent a URL.

Respond with ONLY a JSON object, no other text:
{{"poster_kind": "employer|agency|unknown", "end_client": "<name or null>", "website_url": "<https URL or null>"}}
