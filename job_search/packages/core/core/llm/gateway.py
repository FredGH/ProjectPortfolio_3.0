"""The LLM gateway's public entrypoint: `llm.complete(task=..., ...)`.

This is the module every future LLM-calling step imports — Step 13's CV
extraction, Step 15's re-rank, Step 17's tailoring and critic, and so on.
None of them ever choose a provider directly; they name a task and this
module resolves it (DECISIONS.md §1).
"""

from __future__ import annotations

from pathlib import Path

from core.llm.call_log import log_llm_call
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter, LLMResponse


def complete(
    task: str,
    prompt: str,
    *,
    prompt_version: str,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    seed: int | None = None,
) -> LLMResponse:
    """Run a completion for `task`, routed to its configured provider.

    Args:
        task: The task name, resolved against `config/llm_tasks.yml`.
        prompt: The prompt text to send.
        prompt_version: The versioned prompt identifier that produced
            `prompt` — stamped onto the call log and, later, onto every
            generated artefact row.
        adapters: Every available adapter, keyed by provider name (e.g.
            `{"ollama": OllamaAdapter(...), "anthropic": AnthropicAdapter(...)}`).
            Callers construct and inject these explicitly rather than the
            gateway constructing them, so tests never need real credentials
            or network access.
        config_path: Path to the task-config YAML. Defaults to
            `config/llm_tasks.yml` at the repository root.
        provider: Overrides the task's configured provider. Used by the
            eval harness (PLAN.md Step 12a) to force a specific provider
            for an eval run, independent of what the task routes to in
            production. `None` (the default) resolves from
            `config/llm_tasks.yml` as before — every existing caller is
            unaffected.
        model: Overrides the task's configured model. Must be given
            together with `provider` — an override that supplies one
            without the other resolves the missing one from task config,
            which is almost never what an eval-time caller wants.
        temperature: Sampling temperature, passed straight through to the
            adapter. Defaults to 0.0 for maximum reproducibility.
        seed: A fixed seed, passed straight through — honoured by Ollama,
            ignored by Anthropic (see `AnthropicAdapter.complete`).

    Returns:
        The adapter's `LLMResponse`.

    Raises:
        core.llm.task_config.TaskConfigError: If `task` has no entry in the
            task config file and no full override is given.
        KeyError: If the resolved provider has no matching entry in
            `adapters`.
    """
    if provider is None or model is None:
        task_config = load_task_config(task, config_path=config_path)
        provider = provider or task_config.provider
        model = model or task_config.model
    adapter = adapters[provider]
    response = adapter.complete(
        model=model, prompt=prompt, temperature=temperature, seed=seed
    )
    log_llm_call(task=task, response=response, prompt_version=prompt_version)
    return response
