from __future__ import annotations

import httpx

from agentic_triage import settings
from agentic_triage.core.config import DomainConfig
from agentic_triage.core.state import TriageState

_HYDE_PROMPT = """\
You are a {domain} compliance expert. Write a short passage (2–3 sentences) that \
would appear in a knowledge base directly relevant to the following complaint. \
Focus on the regulatory or financial dimension.
Output only the passage — no preamble, no explanation.

Complaint: {text}
"""


def make_hyde_node(config: DomainConfig):
    async def hyde(state: TriageState) -> dict:
        prompt = _HYDE_PROMPT.format(
            domain=config.domain_name,
            text=state["sanitized_text"],
        )
        resp = await _call_ollama(prompt)
        return {"hyde_text": resp.strip()}

    return hyde


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
