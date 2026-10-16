"""A slim API for the Offer Check page.

The full API also imports the CV-extraction stack (docling), which a plain
Python environment may not have. Used by `scripts/demo_offers_ui.sh`.
"""

from __future__ import annotations

from app.routers import offers, market
from fastapi import FastAPI

app = FastAPI(title="Offer Check demo API")
app.include_router(offers.router)
app.include_router(market.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the process is serving requests.

    Returns:
        A fixed `{"status": "ok"}` payload.
    """
    return {"status": "ok"}
