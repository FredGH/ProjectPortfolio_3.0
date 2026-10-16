"""The novelty index (PLAN.md Step 21b).

novelty = recency x employer breadth x growth velocity, each in [0, 1]. The
middle factor does most of the work: a title that appeared at one company is
noise, the same title at eleven unrelated companies is a market forming. A hard
floor enforces it: below `min_employers` the index is None ("noise"), never a
small number that could be misread as a weak trend.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from core.emergent.config import EmergentConfig
from core.emergent.jobs import DiscoveryJob

RECENCY_HORIZON_DAYS = 365.0


@dataclass(frozen=True)
class Novelty:
    """A novelty result.

    Attributes:
        value: The index in [0, 1], or None.
        note: Why it is None, or a short description of the factors.
    """

    value: float | None
    note: str


def _weekly_new_employers(jobs: list[DiscoveryJob]) -> list[int]:
    """Count employers first appearing in each week.

    Args:
        jobs: The jobs of one title or cluster.

    Returns:
        New-employer counts per ISO week, oldest first (weeks with none are
        omitted: only weeks in which something arrived are periods).
    """
    first_week: dict[str, tuple[int, int]] = {}
    for job in sorted(jobs, key=lambda j: j.first_seen_at):
        if job.employer and job.employer not in first_week:
            iso = job.first_seen_at.isocalendar()
            first_week[job.employer] = (iso.year, iso.week)
    per_week: dict[tuple[int, int], int] = defaultdict(int)
    for week in first_week.values():
        per_week[week] += 1
    return [per_week[w] for w in sorted(per_week)]


def novelty_index(
    jobs: list[DiscoveryJob],
    config: EmergentConfig,
    now: datetime,
    corpus_start: datetime,
) -> Novelty:
    """Score how novel a title or cluster is.

    Args:
        jobs: Its non-agency discovery jobs.
        config: The emergent config (employer floor, minimum periods).
        now: The reference time.
        corpus_start: When collection began (the earliest first-seen of the
            whole corpus): a title already present then cannot be shown to be
            new, however long ago that is.

    Returns:
        The index; None with a note when below the employer floor, when the
        title is baseline, or when fewer than `min_periods` weekly periods
        exist to measure velocity.
    """
    employers = {j.employer for j in jobs if j.employer}
    if len(employers) < config.min_employers:
        return Novelty(
            None,
            f"noise: {len(employers)} employer(s), floor is {config.min_employers}",
        )
    first_seen = min(j.first_seen_at for j in jobs)
    if (first_seen - corpus_start).days < config.baseline_days:
        return Novelty(
            None, "baseline: present since collection began, so not shown to be new"
        )
    weekly = _weekly_new_employers(jobs)
    if len(weekly) < config.min_periods:
        return Novelty(
            None, f"{len(weekly)} of {config.min_periods} periods: velocity unknown"
        )
    age_days = max(0.0, (now - first_seen).total_seconds() / 86400.0)
    recency = max(0.0, 1.0 - age_days / RECENCY_HORIZON_DAYS)
    breadth = min(1.0, len(employers) / (2.0 * config.min_employers))
    quarter = max(1, len(weekly) // 4)
    early = sum(weekly[:quarter]) / quarter
    late = sum(weekly[-quarter:]) / quarter
    growth = (late - early) / max(early, 1.0)
    velocity = 0.5 + 0.5 * math.tanh(growth)
    value = recency * breadth * velocity
    return Novelty(
        value,
        f"recency {recency:.2f} x breadth {breadth:.2f} x velocity {velocity:.2f}",
    )
