"""Build `emergent.title_canonical` from the discovery corpus (PLAN.md Step 21b)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from core.emergent.canonical import canonicalise, is_stoplisted
from core.emergent.config import EmergentConfig
from core.emergent.jobs import DiscoveryJob

_EXAMPLES = 5


@dataclass
class TitleRow:
    """One canonical title and its footprint.

    Attributes:
        canonical_title: The canonical phrase.
        first_seen_at: Earliest first-seen among its jobs.
        last_seen_at: Latest first-seen among its jobs.
        employers: Distinct non-agency employers.
        sources: Distinct sources.
        postings: Number of jobs.
        examples: A few distinct raw titles.
        job_ids: The jobs behind it.
    """

    canonical_title: str
    first_seen_at: datetime
    last_seen_at: datetime
    employers: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    postings: int = 0
    examples: list[str] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TitleStats:
    """What building the titles dropped.

    Attributes:
        jobs: Jobs considered (agencies already removed).
        stoplisted: Jobs dropped for title inflation.
        empty: Jobs whose title reduced to nothing.
    """

    jobs: int
    stoplisted: int
    empty: int


def build_title_rows(
    jobs: list[DiscoveryJob], config: EmergentConfig
) -> tuple[list[TitleRow], TitleStats]:
    """Group jobs by canonical title.

    Args:
        jobs: Non-agency discovery jobs (agencies are filtered upstream).
        config: The emergent config.

    Returns:
        The canonical titles, oldest first, and what was dropped.
    """
    rows: dict[str, TitleRow] = {}
    stoplisted = empty = 0
    for job in jobs:
        if is_stoplisted(job.title, config):
            stoplisted += 1
            continue
        phrase = canonicalise(job.title, config, job.company)
        if phrase is None:
            empty += 1
            continue
        row = rows.get(phrase)
        if row is None:
            row = rows[phrase] = TitleRow(phrase, job.first_seen_at, job.first_seen_at)
        row.first_seen_at = min(row.first_seen_at, job.first_seen_at)
        row.last_seen_at = max(row.last_seen_at, job.first_seen_at)
        if job.employer:
            row.employers.add(job.employer)
        row.sources.add(job.source)
        row.postings += 1
        row.job_ids.append(job.job_group_id)
        if job.title not in row.examples and len(row.examples) < _EXAMPLES:
            row.examples.append(job.title)
    ordered = sorted(rows.values(), key=lambda r: (r.first_seen_at, r.canonical_title))
    return ordered, TitleStats(len(jobs), stoplisted, empty)


def titles_by_phrase(
    jobs: list[DiscoveryJob], config: EmergentConfig
) -> dict[str, list[DiscoveryJob]]:
    """Map each canonical title to its jobs.

    Args:
        jobs: Non-agency discovery jobs.
        config: The emergent config.

    Returns:
        Canonical title to the jobs behind it (stoplisted and empty titles
        are absent).
    """
    grouped: dict[str, list[DiscoveryJob]] = defaultdict(list)
    for job in jobs:
        phrase = canonicalise(job.title, config, job.company)
        if phrase is not None and not is_stoplisted(job.title, config):
            grouped[phrase].append(job)
    return dict(grouped)
