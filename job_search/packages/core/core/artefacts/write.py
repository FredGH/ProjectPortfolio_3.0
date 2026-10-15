"""The writer call (PLAN.md Step 19): ask Claude for sentences with sources."""

from __future__ import annotations

import json
from pathlib import Path

from core.artefacts.context import EvidencePack
from core.artefacts.schema import Kind
from core.company_intel.llm import TaskRun, run_task
from core.llm.types import LLMAdapter

WRITER_TASKS = {"cover_letter": "artefact_letter", "pitch": "artefact_pitch"}
"""The LLM task of each document type."""

_PROMPT_VERSION = 2


def render_evidence(pack: EvidencePack) -> str:
    """List the evidence for the prompt, one `[id] text` line each.

    Args:
        pack: The evidence pack.

    Returns:
        The lines; any newline inside an evidence text becomes a space so each
        id stays on one line.
    """
    return "\n".join(
        f"[{item.id}] {' '.join(item.text.split())}" for item in pack.items.values()
    )


def write(
    kind: Kind,
    pack: EvidencePack,
    *,
    feedback: str,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> TaskRun:
    """Ask the writer for a document.

    Args:
        kind: `cover_letter` or `pitch`.
        pack: The evidence pack.
        feedback: What the previous attempt got wrong, or "" for the first.
        adapters: Provider name to LLM adapter.
        config_path: Task-config override (tests).

    Returns:
        The reply and its usage record.

    Raises:
        core.company_intel.llm.LlmTaskError: If the reply was cut off.
    """
    return run_task(
        WRITER_TASKS[kind],
        _PROMPT_VERSION,
        {
            "identity": pack.identity,
            "company": pack.company or "(not stated)",
            "title": json.dumps(pack.title),
            "evidence": render_evidence(pack),
            "feedback": feedback,
        },
        adapters,
        config_path=config_path,
        max_tokens=8192,
    )
