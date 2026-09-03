"""DB engines and the per-request tenancy session context.

Every per-user query must run inside `session_scope(app_engine,
user_id=...)` — that's what sets the `app.current_user_id` GUC every RLS
policy in this project keys on. There is no other sanctioned way to set it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, Request
from sqlalchemy import Connection, Engine, create_engine, text


def build_engine(dsn: str) -> Engine:
    """Build a SQLAlchemy engine for the given DSN.

    Args:
        dsn: A `postgresql+psycopg://...` connection string — either the
            migration/owner DSN or the `job_search_app` DSN.

    Returns:
        A configured `Engine`. Callers are expected to reuse it (one per
        process), not build one per request.
    """
    return create_engine(dsn, pool_pre_ping=True)


@contextmanager
def session_scope(
    engine: Engine, *, user_id: uuid.UUID | None = None
) -> Iterator[Connection]:
    """Open one transaction, optionally scoped to a user via RLS.

    Args:
        engine: The engine to connect through — the app-role engine for any
            per-user query, the migration/owner engine only for
            administrative work that must see across users.
        user_id: When given, sets `app.current_user_id` for the lifetime of
            this transaction via `SET LOCAL`, so every RLS policy in the
            database scopes to this user. When omitted, no GUC is set, so
            an app-role connection sees zero rows of any per-user table
            (fail-closed) and a migration-role connection sees everything
            (it bypasses RLS as the table owner regardless).

    Yields:
        A `Connection` with the transaction open.
    """
    with engine.connect() as conn:
        with conn.begin():
            if user_id is not None:
                # `SET LOCAL` does not support bound parameters; `user_id`
                # is a `uuid.UUID`, not client-supplied text, so its `str()`
                # is a validated UUID literal — safe to interpolate.
                conn.execute(text(f"SET LOCAL app.current_user_id = '{user_id}'"))
            else:
                # Set to a nil UUID that will never match real user IDs, ensuring
                # RLS policies return no rows (fail-closed). The nil UUID was
                # chosen to avoid an empty string casting error with RLS policies.
                conn.execute(
                    text(
                        "SET LOCAL app.current_user_id = "
                        "'00000000-0000-0000-0000-000000000000'"
                    )
                )
            yield conn


def get_current_user_id(request: Request) -> uuid.UUID:
    """FastAPI dependency resolving the authenticated user's ID.

    Deliberately never reads a client-supplied header or query parameter —
    PLAN.md Step 1a requires the session user context come "from a verified
    token only." No verified-token source exists yet (that's Step 22a's
    IAP integration), so this raises until `request.state.user_id` has been
    set by that future middleware. This is the seam Step 22a fills in, not
    a stand-in that trusts anything from the request.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The authenticated user's ID.

    Raises:
        fastapi.HTTPException: 501, when no identity middleware has set
            `request.state.user_id` yet.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(
            status_code=501,
            detail="Authentication not implemented yet — see Step 22a.",
        )
    return user_id
