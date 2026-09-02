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
    """

    task: str
    provider: Literal["ollama", "anthropic"]
    model: str
    prompt_family: str


def load_task_config(task: str, config_path: Path | None = None) -> TaskConfig:
    """Resolve a task's provider/model configuration from YAML.

    Args:
        task: The task name to resolve, e.g. "skill_extraction".
        config_path: Path to the task-config YAML file. Defaults to
            `config/llm_tasks.yml` at the repository root.

    Returns:
        The resolved `TaskConfig` for the requested task.

    Raises:
        TaskConfigError: If `task` has no entry in the config file.
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
    return TaskConfig(
        task=task,
        provider=entry["provider"],
        model=entry["model"],
        prompt_family=entry["prompt_family"],
    )
