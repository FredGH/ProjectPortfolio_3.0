"""The bank critic (PLAN.md Step 20): technical correctness, on Claude only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.artefacts.critic import CriticConfigError, CriticError, verdict_from_entry
from core.company_intel.llm import LlmTaskError, run_task
from core.llm.json_response import parse_json_response
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.tailoring.critic import Verdict

TASK = "interview_critic"
REQUIRED_PROVIDER = "anthropic"
PROMPT_VERSION_NUMBER = 1

__all__ = ["CriticConfigError", "CriticError", "assert_critic_provider", "judge"]


@dataclass(frozen=True)
class Judged:
    """One critic call's outcome.

    Attributes:
        verdicts: A verdict per item id; unanswered items are unsupported.
        model: The model that answered.
        prompt_version: The prompt version used.
        record: The usage record of the call.
    """

    verdicts: dict[str, Verdict]
    model: str
    prompt_version: str
    record: dict


def assert_critic_provider(config_path: Path | None = None) -> None:
    """Refuse to proceed unless the critic is routed to Anthropic.

    Args:
        config_path: Task-config override (tests).

    Raises:
        CriticConfigError: If `interview_critic` resolves elsewhere.
    """
    provider = load_task_config(TASK, config_path).provider
    if provider != REQUIRED_PROVIDER:
        raise CriticConfigError(
            f"{TASK} must run on {REQUIRED_PROVIDER!r} (a guard validated on a "
            f"weaker model gives false confidence), but is routed to {provider!r}"
        )


def judge(
    items: list[dict],
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> Judged:
    """Judge a batch of Q&As.

    Args:
        items: Dicts with `id`, `kind`, `question` and `model_answer`.
        adapters: LLM adapters by provider; must include `anthropic`.
        config_path: Task-config override (tests).

    Returns:
        The verdicts; an item the critic did not answer is unsupported.

    Raises:
        CriticConfigError: If the critic is not routed to Anthropic.
        CriticError: If the reply is unusable or cut off (carries the call
            cost in `calls`).
    """
    assert_critic_provider(config_path)
    try:
        run = run_task(
            TASK,
            PROMPT_VERSION_NUMBER,
            {"items": json.dumps(items, indent=2)},
            adapters,
            config_path=config_path,
            max_tokens=4096,
        )
    except LlmTaskError as exc:
        raise CriticError(f"the critic's reply was cut off: {exc}") from exc
    try:
        data = parse_json_response(run.text)
        answered: dict[str, Verdict] = {}
        for entry in data.get("verdicts", []):  # type: ignore[attr-defined]
            verdict = verdict_from_entry(entry)
            previous = answered.get(verdict.item_id)
            if previous is None or (previous.supported and not verdict.supported):
                answered[verdict.item_id] = verdict
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
        ValueError,
    ) as exc:
        raise CriticError(f"unusable critic reply: {exc}", [run.record]) from exc
    verdicts = {
        item["id"]: answered.get(
            item["id"], Verdict(item["id"], False, "the critic gave no verdict")
        )
        for item in items
    }
    return Judged(verdicts, run.model, run.prompt_version, run.record)
