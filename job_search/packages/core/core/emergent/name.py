"""Naming a cluster (PLAN.md Step 21b). Every name is a hypothesis.

Claude names and characterises a candidate cluster from its member titles and
skills. It cannot corroborate it (no search tool exists yet), so the result is
stored `uncorroborated` and only a human verdict can confirm it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.company_intel.llm import TaskRun, run_task
from core.emergent.store import StoredCluster
from core.llm.json_response import parse_json_response
from core.llm.types import LLMAdapter

TASK = "emergent_naming"
PROMPT_VERSION = 1
COST_PER_CLUSTER_USD = 0.03
MAX_NAME_CHARS = 80
MAX_TITLES_SHOWN = 40


class NamingError(ValueError):
    """The naming reply could not be used."""


@dataclass(frozen=True)
class Naming:
    """A proposed name.

    Attributes:
        name: The short name.
        characterisation: What the role appears to be.
        run: The task run (model, prompt version, usage).
    """

    name: str
    characterisation: str
    run: TaskRun


def name_cluster(
    cluster: StoredCluster,
    skills: list[str],
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> Naming:
    """Ask Claude to name one cluster.

    Args:
        cluster: The cluster.
        skills: Its top skills.
        adapters: LLM adapters by provider; must include `anthropic`.
        config_path: Task-config override (tests).

    Returns:
        The proposed name and characterisation.

    Raises:
        NamingError: If the reply is not JSON with a usable name and
            characterisation.
        core.company_intel.llm.LlmTaskError: If the reply was cut off.
    """
    run = run_task(
        TASK,
        PROMPT_VERSION,
        {
            "titles": json.dumps(cluster.titles[:MAX_TITLES_SHOWN]),
            "skills": json.dumps(skills),
            "employers": str(cluster.n_employers),
            "nearest": cluster.nearest_category or "none",
        },
        adapters,
        config_path=config_path,
        max_tokens=1024,
    )
    try:
        data = parse_json_response(run.text)
        name = str(data["name"]).strip()
        characterisation = str(data["characterisation"]).strip()
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise NamingError(f"unusable naming reply: {exc}") from exc
    if not name or not characterisation or len(name) > MAX_NAME_CHARS:
        raise NamingError("unusable naming reply: missing or overlong name")
    return Naming(name, characterisation, run)
