"""The fair-use guard on shared API quotas (PLAN.md Step 1a).

One user's heavy ingestion month must not starve the other user's — this
function is the few lines that enforce that, called by Step 3/4's connector
runner before each request against a rate-limited shared source.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Connection, text


def check_and_increment_shared_quota(
    conn: Connection,
    *,
    resource_name: str,
    period_start: datetime.date,
    amount: int = 1,
) -> bool:
    """Atomically check and consume shared quota for one resource/period.

    Args:
        conn: An open connection inside a transaction (typically from
            `session_scope`). The UPDATE below is atomic with respect to
            concurrent callers on the same row because Postgres locks the
            row for the duration of the UPDATE.
        resource_name: The shared resource being consumed, e.g. "adzuna".
        period_start: The billing period this call counts against.
        amount: How many units this call consumes. Defaults to 1.

    Returns:
        `True` if quota was available and has now been consumed. `False`
        if the resource/period has no configured row, or consuming
        `amount` more would exceed `total_limit` — in both cases no row is
        changed, so this is safe to call speculatively before every
        request.
    """
    result = conn.execute(
        text(
            "UPDATE shared_api_quota "
            "SET total_used = total_used + :amount "
            "WHERE resource_name = :resource_name "
            "AND period_start = :period_start "
            "AND total_used + :amount <= total_limit"
        ),
        {
            "amount": amount,
            "resource_name": resource_name,
            "period_start": period_start,
        },
    )
    return result.rowcount > 0
