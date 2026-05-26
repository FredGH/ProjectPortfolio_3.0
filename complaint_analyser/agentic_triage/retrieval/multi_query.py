from __future__ import annotations

import httpx

from agentic_triage import settings
from agentic_triage.core.config import DomainConfig
from agentic_triage.core.state import TriageState

_MULTI_QUERY_PROMPT = """\
Generate {n} distinct search queries to retrieve relevant knowledge-base documents \
for the complaint below.
Each query must focus on a different aspect: \
(1) regulatory exposure, (2) financial impact on the customer, (3) historical precedent.
Output exactly {n} queries, one per line — no numbering, no bullets.

Complaint: {text}
"""


def make_multi_query_node(config: DomainConfig):
    n = config.multi_query_n

    async def multi_query(state: TriageState) -> dict:
        prompt = _MULTI_QUERY_PROMPT.format(n=n, text=state["sanitized_text"])
        raw = await _call_ollama(prompt)
        queries = [q.strip() for q in raw.strip().splitlines() if q.strip()][:n]
        # Prepend cleaned_text so retrieval degrades gracefully on malformed LLM output
        queries = list(dict.fromkeys([state["cleaned_text"]] + queries))
        return {"retrieval_queries": queries}

    return multi_query


async def _call_ollama(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
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
        return r.json()["response"]
