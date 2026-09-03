"""FastAPI entrypoint."""

from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI

from core.db.session import get_current_user_id

app = FastAPI(title="Job Search Platform API")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check.

    Returns:
        A fixed `{"status": "ok"}` payload once the process is serving
        requests.
    """
    return {"status": "ok"}


@app.get("/whoami")
def whoami(user_id: uuid.UUID = Depends(get_current_user_id)) -> dict[str, str]:
    """Return the caller's resolved user ID.

    Args:
        user_id: Injected by `get_current_user_id` — 501s until Step 22a's
            identity middleware is in place.

    Returns:
        The caller's user ID as a string.
    """
    return {"user_id": str(user_id)}
