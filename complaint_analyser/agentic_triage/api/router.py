from __future__ import annotations

import uuid
from typing import Annotated

from arq import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request

from agentic_triage.api.models import (
    BatchItem,
    BatchStatusResponse,
    BatchSubmitResponse,
    ReportRequest,
    ReportResponse,
)
from agentic_triage.reporting.reporter import generate_summary

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_redis(request: Request) -> ArqRedis:
    return request.app.state.redis


def get_db(request: Request):
    return request.app.state.db  # asyncpg Pool — wired in step 12


RedisDep = Annotated[ArqRedis, Depends(get_redis)]
DbDep = Annotated[object, Depends(get_db)]


# ---------------------------------------------------------------------------
# Batch endpoints
# ---------------------------------------------------------------------------


@router.post("/batch/submit/{domain}", response_model=BatchSubmitResponse)
async def batch_submit(
    domain: str,
    items: list[BatchItem],
    redis: RedisDep,
    db: DbDep,
    request: Request,
) -> BatchSubmitResponse:
    _validate_domain(domain, request)

    batch_id = str(uuid.uuid4())

    # Step 12 will replace these stubs with real asyncpg calls.
    await _insert_batch(db, batch_id, domain, len(items))

    already_done = 0
    for item in items:
        if await _is_already_done(db, item.input_id):
            await _increment_batch_counter(db, batch_id, outcome="done")
            already_done += 1
            continue
        await redis.enqueue_job(
            "fast_preprocess_task",
            item.input_id,
            batch_id,
            domain,
            item.text,
            _queue_name="fast",
        )

    return BatchSubmitResponse(
        batch_id=batch_id,
        enqueued=len(items) - already_done,
        already_done=already_done,
    )


@router.get("/batch/{batch_id}/status", response_model=BatchStatusResponse)
async def batch_status(batch_id: str, db: DbDep) -> BatchStatusResponse:
    row = await _fetch_batch(db, batch_id)
    if row is None:
        raise HTTPException(status_code=404, detail="batch_id not found")
    return BatchStatusResponse(**row)


# ---------------------------------------------------------------------------
# Report endpoint
# ---------------------------------------------------------------------------


@router.post("/report/{domain}", response_model=ReportResponse)
async def report(
    domain: str,
    body: ReportRequest,
    db: DbDep,
    request: Request,
) -> ReportResponse:
    _validate_domain(domain, request)
    results = await _fetch_batch_results(db, body.batch_id)
    summary = await generate_summary(domain, results)
    return ReportResponse(batch_id=body.batch_id, domain=domain, summary=summary)


# ---------------------------------------------------------------------------
# Helpers — stubs replaced by real DB calls in step 12
# ---------------------------------------------------------------------------


def _validate_domain(domain: str, request: Request) -> None:
    if domain not in request.app.state.configs:
        raise HTTPException(status_code=404, detail=f"Unknown domain: {domain!r}")


async def _insert_batch(db, batch_id: str, domain: str, total: int) -> None:
    if db is None:
        return
    await db.execute(
        "INSERT INTO triage_batches (batch_id, domain, total) VALUES ($1, $2, $3)",
        batch_id, domain, total,
    )


async def _is_already_done(db, input_id: str) -> bool:
    if db is None:
        return False
    row = await db.fetchval(
        "SELECT 1 FROM triage_results WHERE input_id = $1", input_id
    )
    return row is not None


async def _increment_batch_counter(db, batch_id: str, outcome: str) -> None:
    if db is None:
        return
    col = "done" if outcome == "done" else "failed"
    await db.execute(
        f"UPDATE triage_batches SET {col} = {col} + 1 WHERE batch_id = $1",
        batch_id,
    )


async def _fetch_batch(db, batch_id: str) -> dict | None:
    if db is None:
        return {"total": 0, "done": 0, "failed": 0, "completed_at": None}
    row = await db.fetchrow(
        "SELECT total, done, failed, completed_at FROM triage_batches WHERE batch_id = $1",
        batch_id,
    )
    return dict(row) if row else None


async def _fetch_batch_results(db, batch_id: str) -> list[dict]:
    if db is None:
        return []
    rows = await db.fetch(
        "SELECT * FROM triage_results WHERE batch_id = $1 ORDER BY priority",
        batch_id,
    )
    return [dict(r) for r in rows]
