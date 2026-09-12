"""Per-task LLM provider/model resolution.

Model resolves per TASK from `config/llm_tasks.yml`, never from a single
global provider switch — see DECISIONS.md §1. This is what lets the
local/target boundary move one task at a time instead of forcing an
all-or-nothing migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "llm_tasks.yml"


class TaskConfigError(Exception):
    """Raised when a requested task has no entry in the task config file."""


@dataclass(frozen=True)
class TaskConfig:
    """Resolved provider/model configuration for one LLM task.

    Attributes:
        task: The task name, e.g. "skill_extraction".
        provider: Which adapter serves this task.
        model: The provider-specific model identifier.
        prompt_family: Which prompt variant family to load — prompts are
            versioned per (task, model_family) and never converted between
            families (DECISIONS.md §1).
        eval_metric: Which `core.evals.metrics` function grades this
            task's output, e.g. "exact_match". `None` if this task has
            no eval configured yet.
        eval_regression_threshold: How far a re-run's score may drop
            below the prior run before `run-evals` reports a regression.
            `None` if unconfigured.
        local_provider: The provider to use when the eval harness is
            asked to run this task's golden set against "local" instead
            of its production-configured provider. `None` if no local
            variant is configured for this task (PLAN.md Step 12a: not
            every task has a tuned local prompt).
        local_model: The model to use with `local_provider`.
        local_prompt_family: The prompt family to load with
            `local_provider`.
    """

    task: str
    provider: Literal["ollama", "anthropic"]
    model: str
    prompt_family: str
    eval_metric: str | None = None
    eval_regression_threshold: float | None = None
    local_provider: str | None = None
    local_model: str | None = None
    local_prompt_family: str | None = None


def load_task_config(task: str, config_path: Path | None = None) -> TaskConfig:
    """Resolve a task's provider/model configuration from YAML.

    Args:
        task: The task name to resolve, e.g. "skill_extraction".
        config_path: Path to the task-config YAML file. Defaults to
            `config/llm_tasks.yml` at the repository root.

    Returns:
        The resolved `TaskConfig` for the requested task.

    Raises:
        TaskConfigError: If `task` has no entry in the config file, or
            if its entry sets `eval_metric` without also setting
            `eval_regression_threshold` (which would leave regression
            detection permanently, silently disabled for that task).
    """
    path = config_path or _DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text())
    tasks = raw.get("tasks", {}) if raw else {}

    if task not in tasks:
        raise TaskConfigError(
            f"No task-config entry for {task!r} in {path}. "
            f"Known tasks: {sorted(tasks)}"
        )

    entry = tasks[task]
    eval_metric = entry.get("eval_metric")
    eval_regression_threshold = entry.get("eval_regression_threshold")
    if eval_metric is not None and eval_regression_threshold is None:
        raise TaskConfigError(
            f"Task {task!r} has eval_metric={entry.get('eval_metric')!r} "
            "configured but no eval_regression_threshold — regression "
            "detection would silently never fire. Set "
            "eval_regression_threshold in config/llm_tasks.yml."
        )
    return TaskConfig(
        task=task,
        provider=entry["provider"],
        model=entry["model"],
        prompt_family=entry["prompt_family"],
        eval_metric=eval_metric,
        eval_regression_threshold=eval_regression_threshold,
        local_provider=entry.get("local_provider"),
        local_model=entry.get("local_model"),
        local_prompt_family=entry.get("local_prompt_family"),
    )
