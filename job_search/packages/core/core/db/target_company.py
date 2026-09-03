"""The target_company registry — which ATS boards Greenhouse-style
connectors poll (PLAN.md Step 4).

SHARED reference data (docs/tenancy.md): no user_id, no RLS — every user
sees the same company list. Read via a plain Connection, not
core.db.session.session_scope, because that context manager's whole job is
setting the RLS GUC, which this table has no policy keyed on.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class TargetCompany:
    """One company whose ATS board a connector should poll.

    Attributes:
        id: Primary key.
        name: Display name, e.g. "Airbnb".
        ats_provider: Which ATS this company's board_slug belongs to —
            "greenhouse", "lever", or "ashby" (only "greenhouse" has a
            connector implemented as of Step 4).
        board_slug: The ATS-specific board identifier, e.g. Greenhouse's
            `https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs`.
        active: Whether this company should currently be polled.
        added_at: When this row was created.
    """

    id: uuid.UUID
    name: str
    ats_provider: str
    board_slug: str
    active: bool
    added_at: datetime.datetime


def list_active_companies(
    conn: Connection, *, ats_provider: str
) -> list[TargetCompany]:
    """List every active company registered for a given ATS provider.

    Args:
        conn: An open connection (app-role or owner-role — this table has
            no RLS, so either works).
        ats_provider: Which provider to filter to, e.g. "greenhouse".

    Returns:
        Every `TargetCompany` row with this `ats_provider` and
        `active = true`, ordered by name.
    """
    rows = conn.execute(
        text(
            "SELECT id, name, ats_provider, board_slug, active, added_at "
            "FROM target_company "
            "WHERE ats_provider = :ats_provider AND active = true "
            "ORDER BY name"
        ),
        {"ats_provider": ats_provider},
    ).all()
    return [
        TargetCompany(
            id=row.id,
            name=row.name,
            ats_provider=row.ats_provider,
            board_slug=row.board_slug,
            active=row.active,
            added_at=row.added_at,
        )
        for row in rows
    ]


def upsert_target_company(
    conn: Connection,
    *,
    name: str,
    ats_provider: str,
    board_slug: str,
    active: bool = True,
) -> None:
    """Insert or update one company's registry row.

    Args:
        conn: An open connection inside a transaction (caller commits).
        name: Display name.
        ats_provider: "greenhouse", "lever", or "ashby".
        board_slug: The ATS-specific board identifier.
        active: Whether this company should currently be polled.

    Idempotent on (ats_provider, board_slug) — re-running with the same
    pair updates name/active rather than creating a duplicate row, so the
    seed script (Task 6) is safe to re-run.
    """
    conn.execute(
        text(
            "INSERT INTO target_company (name, ats_provider, board_slug, active) "
            "VALUES (:name, :ats_provider, :board_slug, :active) "
            "ON CONFLICT (ats_provider, board_slug) "
            "DO UPDATE SET name = :name, active = :active"
        ),
        {
            "name": name,
            "ats_provider": ats_provider,
            "board_slug": board_slug,
            "active": active,
        },
    )
