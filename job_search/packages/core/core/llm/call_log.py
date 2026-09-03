"""Structured logging for every LLM call — provider, model, tokens, cost."""

from __future__ import annotations

import logging

from core.llm.types import LLMResponse

_logger = logging.getLogger(__name__)


def log_llm_call(
    *, task: str, response: LLMResponse, prompt_version: str
) -> dict[str, object]:
    """Log one LLM call's provider, model, tokens, and prompt version.

    Every call is logged from day one (PLAN.md Step 1) so cost and quality
    are traceable to a specific change rather than to vibes (DECISIONS.md
    §1 / Step 12a).

    Args:
        task: The task name this call served, e.g. "skill_extraction".
        response: The adapter's `LLMResponse`.
        prompt_version: The versioned prompt identifier that produced this
            call's prompt, e.g. "local.v7".

    Returns:
        The structured record that was logged, for callers/tests that want
        to persist or assert on it directly.
    """
    record: dict[str, object] = {
        "task": task,
        "provider": response.provider,
        "model": response.model,
        "prompt_version": prompt_version,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
    }
    _logger.info(
        "llm_call task=%s provider=%s model=%s",
        task,
        response.provider,
        response.model,
        extra=record,
    )
    return record
