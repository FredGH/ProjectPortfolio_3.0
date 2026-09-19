"""Batch write path for silver.job_skill_extraction / job_skill_raw (Step 14).

Extracts skills for every dedup survivor (silver.job_survivorship) that has
a description and no extraction at the current prompt version. Same
"only new work" pattern as `write_job_category`: a routine run never
re-calls the LLM for already-extracted jobs. Each job commits on its own —
local 8B generation on CPU is slow, so a long batch must keep its progress
if interrupted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import Engine, text

from core.llm.types import LLMAdapter
from core.skills.jd_extract import CURRENT_PROMPT_VERSION, extract_jd_skills
from core.skills.normalise import normalise_skill

logger = logging.getLogger(__name__)

_SELECT_PENDING = text(
    "SELECT js.job_group_id, js.winning_description AS description "
    "FROM silver.job_survivorship AS js "
    "LEFT JOIN silver.job_skill_extraction AS e "
    "ON e.job_group_id = js.job_group_id AND e.prompt_version = :prompt_version "
    "WHERE e.job_group_id IS NULL AND js.winning_description IS NOT NULL "
    "AND (CAST(:job_group_ids AS text[]) IS NULL "
    "OR js.job_group_id = ANY(:job_group_ids)) "
    "ORDER BY js.job_group_id LIMIT CAST(:limit AS integer)"
)
_INSERT_EXTRACTION = text(
    "INSERT INTO silver.job_skill_extraction (job_group_id, prompt_version, model) "
    "VALUES (:job_group_id, :prompt_version, :model) "
    "ON CONFLICT (job_group_id, prompt_version) DO NOTHING"
)
_INSERT_RAW = text(
    "INSERT INTO silver.job_skill_raw (job_group_id, prompt_version, raw_skill, "
    "raw_norm, requirement_level) VALUES (:job_group_id, :prompt_version, "
    ":raw_skill, :raw_norm, :requirement_level) ON CONFLICT DO NOTHING"
)


@dataclass(frozen=True)
class WriteSummary:
    """Counts from one `write_job_skills` run.

    Attributes:
        extracted_jobs: Jobs successfully extracted and recorded.
        skill_rows: `job_skill_raw` rows written.
        failed_jobs: Jobs whose extraction failed (retried next run).
    """

    extracted_jobs: int
    skill_rows: int
    failed_jobs: int


def write_job_skills(
    engine: Engine,
    *,
    adapters: dict[str, LLMAdapter],
    job_group_ids: list[str] | None = None,
    limit: int | None = None,
) -> WriteSummary:
    """Extract and store skills for every not-yet-extracted dedup survivor.

    Args:
        engine: The owner-role engine (shared-zone tables).
        adapters: Every available LLM adapter, keyed by provider.
        job_group_ids: Restrict to these jobs; `None` (what the CLI passes)
            covers every pending survivor. Exists so tests never extract
            unrelated rows in the shared dev DB.
        limit: Process at most this many jobs; `None` for all.

    Returns:
        The `WriteSummary`.
    """
    with engine.connect() as conn:
        pending = conn.execute(
            _SELECT_PENDING,
            {
                "prompt_version": CURRENT_PROMPT_VERSION,
                "job_group_ids": job_group_ids,
                "limit": limit,
            },
        ).all()

    extracted = skill_rows = failed = 0
    for row in pending:
        try:
            extraction = extract_jd_skills(row.description, adapters=adapters)
        except (ValueError, httpx.HTTPError) as exc:
            failed += 1
            logger.warning("skill extraction failed for %s: %s", row.job_group_id, exc)
            continue
        raw_rows = [
            {
                "job_group_id": row.job_group_id,
                "prompt_version": extraction.prompt_version,
                "raw_skill": skill.skill.strip(),
                "raw_norm": normalise_skill(skill.skill),
                "requirement_level": skill.requirement_level,
            }
            for skill in extraction.skills
        ]
        with engine.begin() as conn:
            conn.execute(
                _INSERT_EXTRACTION,
                {
                    "job_group_id": row.job_group_id,
                    "prompt_version": extraction.prompt_version,
                    "model": extraction.model or None,
                },
            )
            if raw_rows:
                conn.execute(_INSERT_RAW, raw_rows)
        extracted += 1
        skill_rows += len(raw_rows)
    return WriteSummary(extracted, skill_rows, failed)
