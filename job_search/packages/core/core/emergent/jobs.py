"""The discovery corpus as the emergent pipeline sees it (PLAN.md Step 21b)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class DiscoveryJob:
    """One discovery-channel job.

    Attributes:
        job_group_id: The deduplicated job.
        title: The raw title.
        company: The employer's display name.
        employer: The normalised employer name (None when unknown).
        first_seen_at: When we first saw it.
        source: The first source that delivered it.
        it_term_count: Technology areas its description mentions.
    """

    job_group_id: str
    title: str
    company: str | None
    employer: str | None
    first_seen_at: datetime
    source: str
    it_term_count: int = 0


def load_discovery_jobs(engine: Engine) -> tuple[list[DiscoveryJob], int]:
    """Load the non-agency discovery jobs.

    Args:
        engine: The app-role engine.

    Returns:
        `(jobs, n_agency)`: agency postings are filtered out HERE, before any
        scoring, so agency title churn cannot read as market innovation.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT job_group_id, title_raw, company, normalised_company, "
                "first_seen_at, sources[1] AS source, is_agency, it_term_count "
                "FROM gold.discovery_job WHERE title_raw IS NOT NULL"
            )
        ).all()
    jobs = [
        DiscoveryJob(
            r.job_group_id,
            r.title_raw,
            r.company,
            r.normalised_company,
            r.first_seen_at,
            r.source,
            r.it_term_count,
        )
        for r in rows
        if not r.is_agency
    ]
    return jobs, sum(1 for r in rows if r.is_agency)
