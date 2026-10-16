"""A slim API for the Emergent Roles and Market Marts pages.

The full API also imports the CV-extraction stack (docling), which a plain
Python environment may not have. Used by `scripts/demo_emergent_ui.sh`.
"""

from __future__ import annotations

from app.routers import emergent, market
from fastapi import FastAPI

app = FastAPI(title="Emergent Roles demo API")
app.include_router(emergent.router)
app.include_router(market.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the process is serving requests.

    Returns:
        A fixed `{"status": "ok"}` payload.
    """
    return {"status": "ok"}
