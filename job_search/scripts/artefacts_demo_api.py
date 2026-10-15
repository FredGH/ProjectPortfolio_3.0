"""A slim API for the Cover Letter & Pitch page: just the routers it calls.

The full API also imports the CV-extraction stack (docling), which a plain
Python environment may not have. Used by `scripts/demo_artefacts_ui.sh`.
"""

from __future__ import annotations

from app.routers import artefacts, tailoring
from fastapi import FastAPI

app = FastAPI(title="Cover Letter & Pitch demo API")
app.include_router(artefacts.router)
app.include_router(tailoring.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the process is serving requests.

    Returns:
        A fixed `{"status": "ok"}` payload.
    """
    return {"status": "ok"}
