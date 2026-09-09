"""LLM residual classification (PLAN.md Step 11a, stage 3) — the final
fallback for titles neither the rules nor embedding stage could
confidently classify. Routed via core.llm.gateway's per-task provider
resolution (config/llm_tasks.yml's `job_categorisation` entry, DECISIONS.md
§1) — never a hardcoded provider, and never called for the ~70%+ of
titles the cheaper stages already resolved.
"""

from __future__ import annotations

import json

from core.llm.gateway import complete
from core.llm.types import LLMAdapter

_PROMPT_VERSION = "job_categorisation-v1"

_CATEGORIES = [
    "software_engineer",
    "data_engineer",
    "data_scientist",
    "ai_ml_engineer",
    "analytics_engineer",
    "platform_devops",
    "other",
]

_PROMPT_TEMPLATE = (
    "Classify this job title into exactly one of these categories: "
    "{categories}.\n\n"
    "Job title: {title}\n\n"
    "Respond with ONLY a JSON object, no other text: "
    '{{"category": "<one of the categories above>", "confidence": <float 0-1>}}'
)


def classify_by_llm(
    title: str, *, adapters: dict[str, LLMAdapter]
) -> tuple[str, float]:
    """Classify one title via the LLM gateway's job_categorisation task.

    Args:
        title: The job title to classify.
        adapters: Every available LLM adapter, keyed by provider —
            passed straight through to core.llm.gateway.complete.

    Returns:
        `(category, confidence)`. Falls back to `("other", 0.0)` if the
        response can't be parsed as the expected JSON shape or names a
        category outside the taxonomy — one malformed response should
        not fail the whole classification batch.
    """
    prompt = _PROMPT_TEMPLATE.format(categories=", ".join(_CATEGORIES), title=title)
    response = complete(
        task="job_categorisation",
        prompt=prompt,
        prompt_version=_PROMPT_VERSION,
        adapters=adapters,
    )
    try:
        parsed = json.loads(response.text.strip())
        category = parsed["category"]
        confidence = float(parsed["confidence"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return "other", 0.0
    if category not in _CATEGORIES:
        return "other", 0.0
    return category, confidence
