"""Stage 1 of the scoring funnel: hard filters (PLAN.md Step 15).

Cheap, kills most of the pool before anything expensive runs. A NULL/empty
preference field means "no filter on this dimension" (core.scoring.
preferences.UserPreference already defaults every field this way, so a user
with no saved preferences at all passes every job here).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.normalisation.location import normalise_location
from core.scoring.preferences import read_preference

_SELECT_JOBS = text(
    "SELECT job_group_id, location, engagement_type, ir35_status, "
    "seniority_band, rate_currency, rate_daily_equivalent, rate_annualised, "
    "posted_at FROM gold.dim_job"
)
_ASSUMED_CURRENCY = "GBP"
"""rate_annualised/rate_daily_equivalent are in the job's own rate_currency,
never converted (_gold.yml's documented caveat: comparing them across
currencies is wrong). Preferences are assumed GBP (Step 5a's UK/IR35
context) — a job in another currency cannot be confirmed to clear a floor,
so it is filtered out on that dimension rather than compared unsafely.
Currency-normalised comparison is Step 21a's job, not this one's."""
_SENIORITY_ORDER = ["junior", "mid", "senior", "lead", "principal"]
_UPSERT_PASSED = text(
    "INSERT INTO scoring.job_score (user_id, job_group_id, hard_filter_passed) "
    "VALUES (:user_id, :job_group_id, :passed) "
    "ON CONFLICT (user_id, job_group_id) DO UPDATE SET "
    "hard_filter_passed = EXCLUDED.hard_filter_passed"
)


def _passes(job: dict, pref, as_of: datetime.date) -> bool:
    """Decide whether one job clears one user's hard filters.

    Args:
        job: A row from `_SELECT_JOBS`, as a mapping.
        pref: The user's `UserPreference`.
        as_of: The date to treat as "today" for the posting-age filter.

    Returns:
        True if the job passes every set filter.
    """
    if pref.preferred_locations and job["location"] not in pref.preferred_locations:
        return False
    if pref.contract_types and job["engagement_type"] not in pref.contract_types:
        return False
    if job["ir35_status"] in pref.excluded_ir35_statuses:
        return False
    band = job["seniority_band"]
    if band is not None:
        if pref.min_seniority_band and _SENIORITY_ORDER.index(
            band
        ) < _SENIORITY_ORDER.index(pref.min_seniority_band):
            return False
        if pref.max_seniority_band and _SENIORITY_ORDER.index(
            band
        ) > _SENIORITY_ORDER.index(pref.max_seniority_band):
            return False
    if pref.min_rate_daily or pref.min_salary_annual:
        if job["rate_currency"] not in (None, _ASSUMED_CURRENCY):
            # Cannot safely compare a non-GBP figure to a GBP floor.
            return False
        if pref.min_rate_daily and (
            job["rate_daily_equivalent"] is None
            or job["rate_daily_equivalent"] < pref.min_rate_daily
        ):
            # No day-rate figure at all cannot be confirmed to clear the
            # floor either, so it is filtered out rather than assumed to pass.
            return False
        if pref.min_salary_annual and (
            job["rate_annualised"] is None
            or job["rate_annualised"] < pref.min_salary_annual
        ):
            return False
    if (
        pref.remote_ok == "required"
        and not normalise_location(job["location"]).is_remote
    ):
        return False
    if pref.remote_ok == "excluded" and normalise_location(job["location"]).is_remote:
        return False
    if pref.max_posting_age_days is not None:
        posted_at = job["posted_at"]
        if posted_at is None:
            # Cannot confirm the posting is within the window, same
            # "cannot confirm => filtered out" convention as the missing
            # rate case above.
            return False
        oldest_acceptable = as_of - datetime.timedelta(days=pref.max_posting_age_days)
        if posted_at.date() < oldest_acceptable:
            return False
    return True


def run_hard_filters(
    engine: Engine,
    user_id: uuid.UUID,
    *,
    limit: int | None = None,
    as_of: datetime.date | None = None,
) -> int:
    """Run stage 1 for one user over the whole (or `limit`-capped) job pool.

    Args:
        engine: The app-role engine (RLS-enforced for the write; `dim_job`
            itself is shared, no RLS).
        user_id: Whose preferences to filter by.
        limit: Cap the number of jobs considered (tests only); `None` covers
            every job in `dim_job`.
        as_of: The date to treat as "today" for the posting-age filter
            (injectable so tests are deterministic); defaults to today.

    Returns:
        The number of jobs written (pass or fail; every considered job gets
        a row).
    """
    if as_of is None:
        as_of = datetime.date.today()
    pref = read_preference(engine, user_id)
    query = _SELECT_JOBS
    if limit is not None:
        query = text(query.text + " LIMIT :limit")
    with engine.connect() as conn:
        jobs = (
            conn.execute(query, {"limit": limit} if limit is not None else {})
            .mappings()
            .all()
        )
    with session_scope(engine, user_id=user_id) as conn:
        for job in jobs:
            conn.execute(
                _UPSERT_PASSED,
                {
                    "user_id": user_id,
                    "job_group_id": job["job_group_id"],
                    "passed": _passes(job, pref, as_of),
                },
            )
    return len(jobs)
