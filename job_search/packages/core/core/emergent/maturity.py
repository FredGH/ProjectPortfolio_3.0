"""The maturity gate (PLAN.md Step 21b): refuse a novelty index too early.

Emergent-role detection looks for low-frequency signals against a baseline, so
it needs far more history than volume trends. Below the thresholds the output
is not trustworthy and the API and page must say so instead of showing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.emergent.config import EmergentConfig
from core.emergent.jobs import DiscoveryJob


@dataclass(frozen=True)
class Maturity:
    """How much history the discovery corpus has.

    Attributes:
        history_days: Days between the first and last first-seen date.
        periods: Distinct weeks in which new postings arrived.
        employers: Distinct non-agency employers.
        mature: Whether both history and periods meet their minimums.
        note: What is missing, or empty when mature.
    """

    history_days: float
    periods: int
    employers: int
    mature: bool
    note: str


def assess(jobs: list[DiscoveryJob], config: EmergentConfig) -> Maturity:
    """Assess the corpus's maturity.

    Args:
        jobs: The non-agency discovery jobs.
        config: The emergent config (minimum history and periods).

    Returns:
        The maturity, with a note naming exactly what is still missing.
    """
    if not jobs:
        return Maturity(0.0, 0, 0, False, "no discovery jobs yet")
    seen: list[datetime] = [j.first_seen_at for j in jobs]
    history = (max(seen) - min(seen)).total_seconds() / 86400.0
    weeks = {(d.isocalendar().year, d.isocalendar().week) for d in seen}
    employers = {j.employer for j in jobs if j.employer}
    missing = []
    if history < config.min_history_days:
        missing.append(f"history {history:.0f} of {config.min_history_days} days")
    if len(weeks) < config.min_periods:
        missing.append(f"{len(weeks)} of {config.min_periods} weekly periods")
    return Maturity(
        history_days=history,
        periods=len(weeks),
        employers=len(employers),
        mature=not missing,
        note="; ".join(missing),
    )
