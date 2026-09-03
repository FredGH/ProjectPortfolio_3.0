"""Plain dataclasses mirroring the tenancy tables' columns.

No ORM mapping yet — introduced only when a later step's query needs it
(YAGNI). These types give `core.db.quota` and its tests something typed to
pass around instead of raw tuples.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class AppUser:
    """One row of `app_user`."""

    id: uuid.UUID
    email: str
    display_name: str
    created_at: datetime.datetime
    status: str
    locale: str


@dataclass(frozen=True)
class UserQuota:
    """One row of `user_quota` — one user's caps for one billing period."""

    id: uuid.UUID
    user_id: uuid.UUID
    period_start: datetime.date
    monthly_llm_spend_cap_usd: Decimal
    monthly_llm_spend_used_usd: Decimal
    artefact_generation_cap: int
    artefact_generation_used: int
    alert_cap: int
    alert_used: int


@dataclass(frozen=True)
class SharedApiQuota:
    """One row of `shared_api_quota` — an aggregate cap shared by every
    user, e.g. Adzuna's 1,000 calls/month (PLAN.md Step 1a)."""

    id: uuid.UUID
    resource_name: str
    period_start: datetime.date
    total_limit: int
    total_used: int
