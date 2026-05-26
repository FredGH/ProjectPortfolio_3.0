from __future__ import annotations

import httpx

from agentic_triage import settings

_REPORT_PROMPT = """\
You are a {domain} triage analyst. Write a concise executive summary (3–5 sentences) \
of the batch results below. Highlight the most urgent items and any patterns observed. \
Output only the summary — no preamble, no bullet points.

BATCH RESULTS:
- Total complaints processed: {total}
- P1 (Critical): {p1}
- P2 (High):     {p2}
- P3 (Medium):   {p3}
- P4 (Low):      {p4}
- Auto-classified as P4 (no LLM): {auto_p4}

TOP FLAGGED ITEMS (P1 / P2):
{top_items}
"""


async def generate_summary(
    domain: str,
    results: list[dict],
) -> str:
    """Call Ollama to produce an executive summary for a completed batch."""
    counts: dict[str, int] = {}
    auto_p4 = 0
    for r in results:
        p = r.get("priority", "unknown")
        counts[p] = counts.get(p, 0) + 1
        if r.get("is_auto_p4"):
            auto_p4 += 1

    top_items = [
        f"  [{r['input_id']}] {r['priority']} — {r.get('reasoning', '')[:120]}"
        for r in results
        if r.get("priority") in {"P1", "P2"}
    ][:10]

    prompt = _REPORT_PROMPT.format(
        domain=domain,
        total=len(results),
        p1=counts.get("P1", 0),
        p2=counts.get("P2", 0),
        p3=counts.get("P3", 0),
        p4=counts.get("P4", 0),
        auto_p4=auto_p4,
        top_items="\n".join(top_items) or "  (none)",
    )

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{settings.OLLAMA_HOST}/api/generate",
            json={
                "model": settings.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0},
            },
        )
        r.raise_for_status()

    return r.json()["response"].strip()
