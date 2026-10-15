"""A slim API for the Interview Prep page: just the routers it calls.

The full API also imports the CV-extraction stack (docling), which a plain
Python environment may not have. Used by `scripts/demo_interview_ui.sh`.
"""

from __future__ import annotations

from app.routers import interview
from fastapi import FastAPI

app = FastAPI(title="Interview Prep demo API")
app.include_router(interview.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the process is serving requests.

    Returns:
        A fixed `{"status": "ok"}` payload.
    """
    return {"status": "ok"}
