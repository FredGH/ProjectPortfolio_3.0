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

# The nil UUID, used to ensure RLS policies return no rows when no user ID is
# set. Once any SET LOCAL has run on a pooled connection, current_setting()
# returns empty string (not NULL) in later transactions, causing ''::uuid to
# error. Setting the GUC to this nil UUID avoids the error and achieves the
# fail-closed zero-row behavior (matches no real row).
_NIL_USER_ID = uuid.UUID(int=0)


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
    """Open one transaction, scoped to a user via RLS.

    The `app.current_user_id` GUC is always set: to the given user's ID when
    provided, or to _NIL_USER_ID (00000000-0000-0000-0000-000000000000) when
    omitted. The nil UUID matches no real row, achieving the fail-closed
    zero-row behavior for app-role connections without a user context.

    Why always set (never omit)? Once any SET LOCAL has run on a pooled
    connection, current_setting(..., true) returns empty string (not NULL) in
    later transactions within the same session. Trying to cast ''::uuid throws
    an error in RLS policies. Setting the GUC to a valid UUID sidesteps this.

    Args:
        engine: The engine to connect through — the app-role engine for any
            per-user query, the migration/owner engine only for
            administrative work that must see across users.
        user_id: When given, sets `app.current_user_id` to this UUID. When
            omitted, sets it to _NIL_USER_ID, ensuring every RLS policy
            returns no rows (fail-closed).

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
                # Set to the nil UUID to ensure RLS policies return no rows
                # (fail-closed). See _NIL_USER_ID and the docstring above.
                conn.execute(text(f"SET LOCAL app.current_user_id = '{_NIL_USER_ID}'"))
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
