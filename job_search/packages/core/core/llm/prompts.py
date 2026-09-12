"""Prompt registry — versioned prompt files, keyed on (task, model_family).

Prompts are code: versioned files in the repo, never inline in Python
(DECISIONS.md §1). Never convert a prompt between families — write the
target-family variant when ready, keep both.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_ROOT = Path(__file__).resolve().parents[4] / "prompts"


def load_prompt(task: str, model_family: str, version: int) -> str:
    """Load one prompt registry file's raw text.

    Args:
        task: The task name, e.g. "job_categorisation".
        model_family: Which prompt variant family, e.g. "claude" or "local".
        version: The prompt version number.

    Returns:
        The prompt file's raw text, with `{placeholder}` tokens intact
        for the caller to `str.format(...)`.

    Raises:
        FileNotFoundError: If `prompts/<task>/<model_family>.v<version>.md`
            does not exist.
    """
    path = _PROMPTS_ROOT / task / f"{model_family}.v{version}.md"
    return path.read_text()
