"""Batch write path for silver.job_skill_extraction / job_skill_raw (Step 14).

Extracts skills for every dedup survivor (silver.job_survivorship) that has
a description and no extraction at the current prompt version, optionally
restricted to some sources and gold categories (extraction is the slow step: a
local model on CPU takes minutes per job, so a run is scoped to what matters).
Same
"only new work" pattern as `write_job_category`: a routine run never
re-calls the LLM for already-extracted jobs. Each job commits on its own —
local 8B generation on CPU is slow, so a long batch must keep its progress
if interrupted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from sqlalchemy import Engine, text

from core.llm.types import LLMAdapter
from core.skills.jd_extract import CURRENT_PROMPT_VERSION, extract_jd_skills
from core.skills.normalise import normalise_skill

logger = logging.getLogger(__name__)

_PENDING_FROM_WHERE = (
    "FROM silver.job_survivorship AS js "
    "LEFT JOIN silver.job_skill_extraction AS e "
    "ON e.job_group_id = js.job_group_id AND e.prompt_version = :prompt_version "
    "WHERE e.job_group_id IS NULL AND js.winning_description IS NOT NULL "
    "AND (CAST(:job_group_ids AS text[]) IS NULL "
    "OR js.job_group_id = ANY(:job_group_ids)) "
    "AND (CAST(:sources AS text[]) IS NULL "
    "OR js.apply_source_name = ANY(:sources)) "
    "AND (CAST(:categories AS text[]) IS NULL OR EXISTS ("
    "SELECT 1 FROM gold.dim_job AS d WHERE d.job_group_id = js.job_group_id "
    "AND d.category = ANY(:categories))) "
    "AND (CAST(:countries AS text[]) IS NULL OR EXISTS ("
    "SELECT 1 FROM gold.dim_job AS d WHERE d.job_group_id = js.job_group_id "
    "AND d.country_iso = ANY(:countries)))"
)
_SELECT_PENDING = text(
    "SELECT js.job_group_id, js.winning_description AS description "
    f"{_PENDING_FROM_WHERE} "
    "ORDER BY js.job_group_id LIMIT CAST(:limit AS integer)"
)
_COUNT_PENDING = text(f"SELECT count(*) {_PENDING_FROM_WHERE}")
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


def _pending_params(
    job_group_ids: list[str] | None,
    sources: list[str] | None,
    categories: list[str] | None,
    countries: list[str] | None,
) -> dict[str, object]:
    """Build the bind parameters shared by the pending-jobs queries.

    Args:
        job_group_ids: Restrict to these jobs, or None.
        sources: Restrict to jobs whose winning source is one of these, or None.
        categories: Restrict to jobs whose gold category is one of these, or
            None.
        countries: Restrict to jobs whose gold country_iso is one of these,
            or None.

    Returns:
        The parameter dict (an empty list is treated as no filter).
    """
    return {
        "prompt_version": CURRENT_PROMPT_VERSION,
        "job_group_ids": job_group_ids,
        "sources": sources or None,
        "categories": categories or None,
        "countries": countries or None,
    }


def count_pending_jobs(
    engine: Engine,
    *,
    job_group_ids: list[str] | None = None,
    sources: list[str] | None = None,
    categories: list[str] | None = None,
    countries: list[str] | None = None,
) -> int:
    """Count the jobs a `write_job_skills` run with these filters would process.

    Args:
        engine: The owner-role engine.
        job_group_ids: Restrict to these jobs; None for all.
        sources: Restrict to jobs whose winning source is one of these.
        categories: Restrict to jobs whose gold category is one of these; a
            job with no `gold.dim_job` row does not match.
        countries: Restrict to jobs whose `gold.dim_job.country_iso` is one
            of these (e.g. `["GB"]`); a job with no `gold.dim_job` row, or an
            unresolved country, does not match. See
            `core.normalisation.location` for what resolves a location to a
            country.

    Returns:
        The number of pending jobs (before any `limit`).
    """
    with engine.connect() as conn:
        return conn.execute(
            _COUNT_PENDING,
            _pending_params(job_group_ids, sources, categories, countries),
        ).scalar_one()


def write_job_skills(
    engine: Engine,
    *,
    adapters: dict[str, LLMAdapter],
    job_group_ids: list[str] | None = None,
    limit: int | None = None,
    sources: list[str] | None = None,
    categories: list[str] | None = None,
    countries: list[str] | None = None,
    on_job_done: Callable[[str, bool], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> WriteSummary:
    """Extract and store skills for every not-yet-extracted dedup survivor.

    Args:
        engine: The owner-role engine (shared-zone tables).
        adapters: Every available LLM adapter, keyed by provider.
        job_group_ids: Restrict to these jobs; `None` (what the CLI passes)
            covers every pending survivor. Exists so tests never extract
            unrelated rows in the shared dev DB.
        limit: Process at most this many jobs; `None` for all.
        sources: Only jobs whose winning source (`apply_source_name`) is one
            of these, e.g. `["greenhouse"]`; `None` for every source. This also
            keeps leaked test rows and snippet-only sources out of a run.
        categories: Only jobs whose `gold.dim_job.category` is one of these;
            `None` for every category. A job with no `gold.dim_job` row does
            not match.
        countries: Only jobs whose `gold.dim_job.country_iso` is one of these
            (e.g. `["GB"]`); `None` for every country. A job with no
            `gold.dim_job` row, or an unresolved country, does not match.
        on_job_done: Called after each job with its `job_group_id` and whether
            it was extracted (`False` = it failed and will be retried by a
            later run). A successful job's rows are already committed when
            this fires, so a caller can record durable progress from it. An
            exception it raises propagates and ends the batch.
        should_stop: Checked before each job; when it returns `True` the batch
            ends there, leaving the remaining jobs pending. Lets a caller stop
            within one job rather than at the end of the batch.

    Returns:
        The `WriteSummary` of the jobs processed so far.
    """
    with engine.connect() as conn:
        pending = conn.execute(
            _SELECT_PENDING,
            {
                **_pending_params(job_group_ids, sources, categories, countries),
                "limit": limit,
            },
        ).all()

    extracted = skill_rows = failed = 0
    for row in pending:
        if should_stop is not None and should_stop():
            break
        try:
            extraction = extract_jd_skills(row.description, adapters=adapters)
        except (ValueError, httpx.HTTPError) as exc:
            failed += 1
            logger.warning("skill extraction failed for %s: %s", row.job_group_id, exc)
            if on_job_done is not None:
                on_job_done(row.job_group_id, False)
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
        if on_job_done is not None:
            on_job_done(row.job_group_id, True)
    return WriteSummary(extracted, skill_rows, failed)
