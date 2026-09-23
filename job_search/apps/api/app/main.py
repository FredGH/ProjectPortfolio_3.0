"""FastAPI entrypoint."""

from __future__ import annotations

import uuid

from app.routers import classification, cv, dedup, extraction_runs, ingest, skills
from fastapi import Depends, FastAPI

from core.db.session import get_current_user_id

app = FastAPI(title="Job Search Platform API")
app.include_router(ingest.router)
app.include_router(dedup.router)
app.include_router(classification.router)
app.include_router(cv.router)
app.include_router(skills.router)
app.include_router(extraction_runs.router)


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
