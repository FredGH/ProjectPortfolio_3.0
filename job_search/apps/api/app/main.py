"""FastAPI entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Job Search Platform API")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check.

    Returns:
        A fixed `{"status": "ok"}` payload once the process is serving
        requests.
    """
    return {"status": "ok"}
