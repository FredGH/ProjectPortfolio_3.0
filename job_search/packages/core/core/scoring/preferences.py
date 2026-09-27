"""Per-user hard-filter preferences for the scoring funnel (PLAN.md Step 15).

A field left unset means "no filter on this dimension" — never coerced to
excluding or including everything, the same never-default-unknown principle
DECISIONS.md §2.13 states for IR35/engagement type, applied here to
preferences that gate on those same fields.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import Engine, text

from core.db.session import session_scope


@dataclass(frozen=True)
class UserPreference:
    """One user's hard-filter settings.

    Attributes:
        preferred_locations: Locations to filter to; empty means no filter.
        remote_ok: "required" | "preferred" | "no_preference" | "excluded".
        contract_types: Subset of permanent/contract/ftc/interim to filter
            to; empty means no filter.
        excluded_ir35_statuses: IR35 statuses to exclude; empty excludes
            nothing (never assume an exclusion the user didn't state).
        min_seniority_band: Lowest acceptable band (inclusive), or None.
        max_seniority_band: Highest acceptable band (inclusive), or None.
        min_salary_annual: Minimum annualised salary, or None.
        min_rate_daily: Minimum day rate, or None.
        max_posting_age_days: Oldest acceptable posting age, or None.
    """

    preferred_locations: list[str] = field(default_factory=list)
    remote_ok: str = "no_preference"
    contract_types: list[str] = field(default_factory=list)
    excluded_ir35_statuses: list[str] = field(default_factory=list)
    min_seniority_band: str | None = None
    max_seniority_band: str | None = None
    min_salary_annual: float | None = None
    min_rate_daily: float | None = None
    max_posting_age_days: int | None = None


def read_preference(engine: Engine, user_id: uuid.UUID) -> UserPreference:
    """Read a user's preferences, or the all-default value if unset.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose preferences to read.

    Returns:
        The stored `UserPreference`, or `UserPreference()` if this user has
        never saved one — a missing row is not an error, it is "no filters."
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                "SELECT preferred_locations, remote_ok, contract_types, "
                "excluded_ir35_statuses, min_seniority_band, max_seniority_band, "
                "min_salary_annual, min_rate_daily, max_posting_age_days "
                "FROM scoring.user_preference WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        ).one_or_none()
    if row is None:
        return UserPreference()
    return UserPreference(
        preferred_locations=list(row.preferred_locations),
        remote_ok=row.remote_ok,
        contract_types=list(row.contract_types),
        excluded_ir35_statuses=list(row.excluded_ir35_statuses),
        min_seniority_band=row.min_seniority_band,
        max_seniority_band=row.max_seniority_band,
        min_salary_annual=(
            float(row.min_salary_annual) if row.min_salary_annual is not None else None
        ),
        min_rate_daily=(
            float(row.min_rate_daily) if row.min_rate_daily is not None else None
        ),
        max_posting_age_days=row.max_posting_age_days,
    )


_UPSERT = text(
    "INSERT INTO scoring.user_preference (user_id, preferred_locations, "
    "remote_ok, contract_types, excluded_ir35_statuses, min_seniority_band, "
    "max_seniority_band, min_salary_annual, min_rate_daily, "
    "max_posting_age_days, updated_at) "
    "VALUES (:user_id, :preferred_locations, :remote_ok, :contract_types, "
    ":excluded_ir35_statuses, :min_seniority_band, :max_seniority_band, "
    ":min_salary_annual, :min_rate_daily, :max_posting_age_days, now()) "
    "ON CONFLICT (user_id) DO UPDATE SET "
    "preferred_locations = EXCLUDED.preferred_locations, "
    "remote_ok = EXCLUDED.remote_ok, "
    "contract_types = EXCLUDED.contract_types, "
    "excluded_ir35_statuses = EXCLUDED.excluded_ir35_statuses, "
    "min_seniority_band = EXCLUDED.min_seniority_band, "
    "max_seniority_band = EXCLUDED.max_seniority_band, "
    "min_salary_annual = EXCLUDED.min_salary_annual, "
    "min_rate_daily = EXCLUDED.min_rate_daily, "
    "max_posting_age_days = EXCLUDED.max_posting_age_days, "
    "updated_at = now()"
)


def write_preference(
    engine: Engine, user_id: uuid.UUID, preference: UserPreference
) -> None:
    """Create or replace a user's preferences.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose preferences to write.
        preference: The full preference set to store.
    """
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            _UPSERT,
            {
                "user_id": user_id,
                "preferred_locations": preference.preferred_locations,
                "remote_ok": preference.remote_ok,
                "contract_types": preference.contract_types,
                "excluded_ir35_statuses": preference.excluded_ir35_statuses,
                "min_seniority_band": preference.min_seniority_band,
                "max_seniority_band": preference.max_seniority_band,
                "min_salary_annual": preference.min_salary_annual,
                "min_rate_daily": preference.min_rate_daily,
                "max_posting_age_days": preference.max_posting_age_days,
            },
        )
