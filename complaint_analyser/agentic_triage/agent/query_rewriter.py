from __future__ import annotations

import httpx

from agentic_triage import settings
from agentic_triage.core.config import DomainConfig
from agentic_triage.core.state import TriageState

_REWRITE_PROMPT = """\
The complaint below did not retrieve strong evidence from these knowledge-base \
collections: {low_collections}.
Rewrite it as a concise search query (1–2 sentences) that captures the core \
regulatory or financial concern.
Output only the rewritten query — no explanation, no preamble.

Complaint: {text}
"""


def make_query_rewrite_node(config: DomainConfig):  # noqa: ARG001
    async def query_rewrite(state: TriageState) -> dict:
        low_collections = [
            col for col, score in state["retrieval_scores"].items() if score < 0.6
        ] or list(state["retrieval_scores"].keys())

        prompt = _REWRITE_PROMPT.format(
            low_collections=", ".join(low_collections),
            text=state["cleaned_text"],
        )
        rewritten = await _call_ollama(prompt)
        return {
            "cleaned_text": rewritten.strip(),
            "loop_count": state["loop_count"] + 1,
        }

    return query_rewrite


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
