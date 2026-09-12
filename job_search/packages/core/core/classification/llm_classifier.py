"""LLM residual classification (PLAN.md Step 11a, stage 3) — the final
fallback for titles neither the rules nor embedding stage could
confidently classify. Routed via core.llm.gateway's per-task provider
resolution (config/llm_tasks.yml's `job_categorisation` entry, DECISIONS.md
§1) — never a hardcoded provider. Measured against the real dataset,
this stage resolves a majority of ALL titles (not just a small
residual): the rules/embedding stages can only ever return one of the
6 substantive categories, so every genuinely non-engineering title
(the majority of postings in a broad job aggregator) reaches this
stage and correctly resolves to "other" here, alongside the smaller
share of substantive titles the cheaper stages couldn't place.
"""

from __future__ import annotations

import json

from core.llm.gateway import complete
from core.llm.prompts import load_prompt
from core.llm.types import LLMAdapter

_PROMPT_FAMILY = "claude"
_PROMPT_VERSION_NUMBER = 1
_PROMPT_VERSION = f"{_PROMPT_FAMILY}.v{_PROMPT_VERSION_NUMBER}"

_CATEGORIES = [
    "software_engineer",
    "data_engineer",
    "data_scientist",
    "ai_ml_engineer",
    "analytics_engineer",
    "platform_devops",
    "other",
]


def classify_by_llm(
    title: str,
    *,
    adapters: dict[str, LLMAdapter],
    provider: str | None = None,
    model: str | None = None,
    prompt_family: str | None = None,
) -> tuple[str, float, str | None, str | None]:
    """Classify one title via the LLM gateway's job_categorisation task.

    Args:
        title: The job title to classify.
        adapters: Every available LLM adapter, keyed by provider —
            passed straight through to core.llm.gateway.complete.
        provider: Overrides the production-configured provider — used
            by the eval harness (PLAN.md Step 12a) to force a specific
            provider regardless of what `config/llm_tasks.yml` routes
            `job_categorisation` to. `None` (the default) uses
            production routing.
        model: The model to use with `provider`. Must be given together
            with `provider`.
        prompt_family: Which prompt file to load — defaults to this
            module's own `_PROMPT_FAMILY` ("claude") when `provider` is
            given without an explicit `prompt_family`, so an eval run
            forcing a different provider still needs to say which
            prompt variant that provider should use.

    Returns:
        `(category, confidence, prompt_version, model_id)`. Falls back
        to `("other", 0.0, None, None)` if the response can't be parsed
        as the expected JSON shape or names a category outside the
        taxonomy — one malformed response should not fail the whole
        classification batch, and a fallback carries no meaningful
        prompt_version/model_id since nothing was successfully
        classified.
    """
    resolved_family = prompt_family or _PROMPT_FAMILY
    prompt_template = load_prompt(
        "job_categorisation", resolved_family, _PROMPT_VERSION_NUMBER
    )
    prompt = prompt_template.format(categories=", ".join(_CATEGORIES), title=title)
    prompt_version = f"{resolved_family}.v{_PROMPT_VERSION_NUMBER}"
    response = complete(
        task="job_categorisation",
        prompt=prompt,
        prompt_version=prompt_version,
        adapters=adapters,
        provider=provider,
        model=model,
    )
    try:
        parsed = json.loads(response.text.strip())
        category = parsed["category"]
        confidence = float(parsed["confidence"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return "other", 0.0, None, None
    if category not in _CATEGORIES:
        return "other", 0.0, None, None
    return category, confidence, prompt_version, response.model
