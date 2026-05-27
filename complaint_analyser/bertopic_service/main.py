"""BERTopic drift detection service.

Endpoints
---------
GET /healthz          — liveness probe
GET /clusters/diff    — topics new or growing vs. the prior period
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
DRIFT_GROWTH_RATIO = float(os.getenv("DRIFT_GROWTH_RATIO", "2.0"))
DRIFT_MIN_NEW_DOCS = int(os.getenv("DRIFT_MIN_NEW_DOCS", "5"))


# ── Postgres helpers ──────────────────────────────────────────────────────────


async def _fetch_complaints(pool: Any, since: datetime, until: datetime) -> list[str]:
    rows = await pool.fetch(
        "SELECT raw_text FROM triage_results WHERE created_at >= $1 AND created_at < $2",
        since,
        until,
    )
    return [r["raw_text"] for r in rows if r["raw_text"]]


# ── BERTopic helpers ──────────────────────────────────────────────────────────


def _fit_topic_map(docs: list[str]) -> dict[str, int]:
    """Return {topic_label: doc_count} for the given corpus.

    Returns an empty dict when the corpus is too small to cluster.
    """
    if len(docs) < 2:
        return {}

    try:
        from bertopic import BERTopic  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("bertopic package is not installed") from exc

    model = BERTopic(verbose=False)
    topics, _ = model.fit_transform(docs)
    topic_info = model.get_topic_info()
    return {
        str(row["Name"]): int(row["Count"])
        for _, row in topic_info.iterrows()
        if row["Topic"] != -1
    }


def _compute_diff(
    prior: dict[str, int],
    current: dict[str, int],
) -> list[dict[str, Any]]:
    """Return topics that are new or growing rapidly in current vs. prior."""
    results: list[dict[str, Any]] = []
    for label, count in current.items():
        prior_count = prior.get(label, 0)
        if prior_count == 0 and count >= DRIFT_MIN_NEW_DOCS:
            results.append(
                {
                    "topic": label,
                    "status": "new",
                    "count": count,
                    "prior_count": 0,
                }
            )
        elif prior_count > 0 and count / prior_count >= DRIFT_GROWTH_RATIO:
            results.append(
                {
                    "topic": label,
                    "status": "growing",
                    "count": count,
                    "prior_count": prior_count,
                    "growth_ratio": round(count / prior_count, 2),
                }
            )
    return sorted(results, key=lambda x: x["count"], reverse=True)


# ── App lifecycle ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = None
    if DATABASE_URL:
        try:
            import asyncpg  # type: ignore[import]

            pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
            log.info("Connected to Postgres")
        except Exception as exc:
            log.warning(
                "Postgres unavailable (%s) — /clusters/diff will return empty", exc
            )
    app.state.pool = pool
    yield
    if pool:
        await pool.close()


app = FastAPI(title="BERTopic Drift Service", version="0.1.0", lifespan=lifespan)


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/clusters/diff")
async def clusters_diff(days: int = 7) -> dict[str, Any]:
    """Return topics that are new or growing compared to the prior period.

    Query param ``days`` sets the window length for both current and prior
    periods (default: 7).  Requires a connected Postgres instance; returns an
    empty topic list with a ``note`` field when the database is unavailable.
    """
    now = datetime.now(tz=timezone.utc)
    current_start = now - timedelta(days=days)
    prior_start = current_start - timedelta(days=days)

    period_meta = {
        "current_period": {
            "start": current_start.isoformat(),
            "end": now.isoformat(),
        },
        "prior_period": {
            "start": prior_start.isoformat(),
            "end": current_start.isoformat(),
        },
    }

    pool = app.state.pool
    if pool is None:
        return {
            **period_meta,
            "new_and_growing_topics": [],
            "note": "database unavailable",
        }

    try:
        current_docs = await _fetch_complaints(pool, current_start, now)
        prior_docs = await _fetch_complaints(pool, prior_start, current_start)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database error: {exc}") from exc

    try:
        prior_map = _fit_topic_map(prior_docs)
        current_map = _fit_topic_map(current_docs)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    diff = _compute_diff(prior_map, current_map)

    return {
        **period_meta,
        "current_period": {
            **period_meta["current_period"],
            "doc_count": len(current_docs),
            "topic_count": len(current_map),
        },
        "prior_period": {
            **period_meta["prior_period"],
            "doc_count": len(prior_docs),
            "topic_count": len(prior_map),
        },
        "new_and_growing_topics": diff,
    }


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("bertopic_service.main:app", host="0.0.0.0", port=8001, reload=False)
