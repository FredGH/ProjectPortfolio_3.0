"""A slim API for the Market Marts page: just the routers it calls.

The full API also imports the CV-extraction stack (docling), which a plain
Python environment may not have. Used by `scripts/demo_market_ui.sh`.
"""

from __future__ import annotations

from app.routers import market
from fastapi import FastAPI

app = FastAPI(title="Market Marts demo API")
app.include_router(market.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the process is serving requests.

    Returns:
        A fixed `{"status": "ok"}` payload.
    """
    return {"status": "ok"}
