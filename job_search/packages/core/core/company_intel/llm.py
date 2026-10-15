"""Run one company-intel LLM task through the gateway and record its cost."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.llm.gateway import complete
from core.llm.pricing import estimate_cost_usd
from core.llm.prompts import load_prompt
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter


class LlmTaskError(Exception):
    """An LLM task could not produce a usable reply."""


@dataclass(frozen=True)
class TaskRun:
    """One finished task call.

    Attributes:
        text: The reply, stripped.
        model: The model that answered.
        prompt_version: e.g. `claude.v1`.
        record: `{task, model, input_tokens, output_tokens, cost_usd}`.
    """

    text: str
    model: str
    prompt_version: str
    record: dict


def run_task(
    task: str,
    version: int,
    variables: dict[str, str],
    adapters: dict[str, LLMAdapter],
    *,
    config_path: Path | None = None,
    max_tokens: int = 4096,
) -> TaskRun:
    """Format a task's prompt, call its provider and record the usage.

    Args:
        task: A task name in `config/llm_tasks.yml`.
        version: The prompt version number.
        variables: Values for the template's `{placeholders}`. They are
            format values, so braces inside them are safe.
        adapters: Provider name to adapter.
        config_path: Task-config override (tests).
        max_tokens: Cap on the reply length.

    Returns:
        The reply and its usage record.

    Raises:
        LlmTaskError: If the reply was cut off by the token cap.
        KeyError: If the task's provider has no adapter.
    """
    config = load_task_config(task, config_path=config_path)
    template = load_prompt(task, config.prompt_family, version)
    prompt = template.format(**variables)
    prompt_version = f"{config.prompt_family}.v{version}"
    response = complete(
        task,
        prompt,
        prompt_version=prompt_version,
        adapters=adapters,
        config_path=config_path,
        max_tokens=max_tokens,
    )
    if response.truncated:
        raise LlmTaskError(f"the {task} reply was cut off at {max_tokens} tokens")
    cost = estimate_cost_usd(
        response.provider, response.model, response.input_tokens, response.output_tokens
    )
    record = {
        "task": task,
        "model": response.model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": cost,
    }
    return TaskRun(response.text.strip(), response.model, prompt_version, record)


def total_usage(calls: list[dict]) -> dict:
    """Sum a research's LLM calls.

    Args:
        calls: Records from `run_task`.

    Returns:
        `{calls, input_tokens, output_tokens, cost_usd}`; `cost_usd` is None
        when any call is unpriced.
    """
    costs = [call["cost_usd"] for call in calls]
    return {
        "calls": calls,
        "input_tokens": sum(call["input_tokens"] for call in calls),
        "output_tokens": sum(call["output_tokens"] for call in calls),
        "cost_usd": None if any(c is None for c in costs) else sum(costs),
    }
