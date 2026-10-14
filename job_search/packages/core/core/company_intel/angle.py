"""The per-job competitive angle: how this job, and the transformation it
implies, could put the company ahead of its competitors (PLAN.md Step 19a).

It is analysis, not fact. The prompt may use only the job description and
the stored intel; `guard_angle` drops any point that names a competitor the
intel does not list.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import Engine

from core.company_intel import store
from core.company_intel.llm import LlmTaskError, run_task, total_usage
from core.company_intel.schema import (
    AngleAdvantage,
    AngleContent,
    StoredAngle,
    StoredIntel,
)
from core.llm.json_response import parse_json_response
from core.llm.types import LLMAdapter
from core.tailoring.schema import JobContext

_PROMPT_VERSION = 1
_DESCRIPTION_CHARS = 6000
_MAX_ADVANTAGES = 5


def guard_angle(raw: object, competitor_names: set[str]) -> AngleContent:
    """Keep only well-formed, grounded items of a model reply.

    Args:
        raw: The decoded JSON reply.
        competitor_names: Lower-cased names of the listed competitors.

    Returns:
        The angle; an advantage naming an unlisted competitor, or with no
        point, is dropped, and at most five are kept.
    """
    if not isinstance(raw, dict):
        return AngleContent()
    transformation = raw.get("transformation")
    advantages: list[AngleAdvantage] = []
    items = raw.get("advantages")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        competitor = item.get("competitor")
        competitor = competitor.strip() if isinstance(competitor, str) else ""
        point = item.get("point")
        if not isinstance(point, str) or not point.strip():
            continue
        if competitor and competitor.casefold() not in competitor_names:
            continue
        advantages.append(AngleAdvantage(competitor=competitor, point=point.strip()))
        if len(advantages) == _MAX_ADVANTAGES:
            break
    caveats = raw.get("caveats")
    return AngleContent(
        transformation=(
            transformation.strip() if isinstance(transformation, str) else ""
        ),
        advantages=advantages,
        caveats=(
            [c.strip() for c in caveats if isinstance(c, str) and c.strip()]
            if isinstance(caveats, list)
            else []
        ),
    )


def get_or_create_angle(
    engine: Engine,
    job: JobContext,
    intel: StoredIntel,
    *,
    adapters: dict[str, LLMAdapter],
    force: bool = False,
    config_path: Path | None = None,
) -> StoredAngle | None:
    """Return the job's angle for this intel version, writing it if missing.

    Args:
        engine: The app-role engine.
        job: The job (title, company, description).
        intel: The stored intel the angle is built from.
        adapters: Provider name to LLM adapter.
        force: Write a new angle even if one exists for this version.
        config_path: Task-config override (tests).

    Returns:
        The stored angle, or None when the intel holds nothing to reason
        from or the model's reply is unusable.
    """
    if not force:
        existing = store.read_angle(
            engine, job.job_group_id, intel.company_key, intel.version
        )
        if existing is not None:
            return existing
    if intel.content.is_empty():
        return None
    names = [c.name for c in intel.content.competitors]
    try:
        run = run_task(
            "competitive_angle",
            _PROMPT_VERSION,
            {
                "title": job.title_for_display or "",
                "company": intel.display_name,
                "description": job.description[:_DESCRIPTION_CHARS],
                "profile": intel.content.model_dump_json(),
                "competitors": ", ".join(names) or "none listed",
            },
            adapters,
            config_path=config_path,
            max_tokens=2048,
        )
        raw = parse_json_response(run.text)
    except (LlmTaskError, json.JSONDecodeError, ValueError):
        return None
    content = guard_angle(raw, {name.casefold() for name in names})
    if not content.transformation and not content.advantages:
        return None
    return store.append_angle(
        engine,
        job_group_id=job.job_group_id,
        company_key=intel.company_key,
        intel_version=intel.version,
        content=content,
        model=run.model,
        prompt_version=run.prompt_version,
        usage=total_usage([run.record]),
    )
