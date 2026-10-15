You fill in a short fact table about one company from your general knowledge. Nothing here is looked up: it will be shown to a job seeker as approximate and unverified, so honesty matters more than completeness.

Company: {company}

What the company's own website says (may be empty):
{facts}

Rules:
- Answer each row in one short line. Be approximate and say so ("about", "roughly", a range) when you are not exact.
- If you do not know a row, or only have a vague guess, set "value" to "not known" and "as_of" to null. A "not known" is far better than an invented number. Market share and employee satisfaction are often not known.
- "as_of" is the year your figure refers to (for example "2023"), as a string, or null.
- "industry": the sector or sectors it operates in. "ranking": its position among its industry peers, with the measure (for example "one of the top 5 by revenue"). "market_share": a share of a named market, only if you know a credible figure. "growth_5y": how revenue (or the best available measure, say which) changed over the last five years. "employees": headcount. "satisfaction": employee satisfaction, for example an approximate review-site score, naming the source.
- Do not contradict the website facts above; if they give a headcount, use it.

Respond with ONLY a JSON object, no other text:
{{"industry": {{"value": "...", "as_of": "2024"}}, "ranking": {{"value": "...", "as_of": null}}, "market_share": {{"value": "not known", "as_of": null}}, "growth_5y": {{"value": "...", "as_of": "2024"}}, "employees": {{"value": "...", "as_of": "2024"}}, "satisfaction": {{"value": "not known", "as_of": null}}}}
