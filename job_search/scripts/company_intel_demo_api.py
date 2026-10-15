"""A slim API for the Company Intel page: just the routers the page calls.

The full API also imports the CV-extraction stack (docling), which a plain
Python environment may not have. Used by `scripts/demo_company_intel_ui.sh`.
"""

from __future__ import annotations

from app.routers import intel, tailoring
from fastapi import FastAPI

app = FastAPI(title="Company Intel demo API")
app.include_router(intel.router)
app.include_router(tailoring.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the process is serving requests.

    Returns:
        A fixed `{"status": "ok"}` payload.
    """
    return {"status": "ok"}
