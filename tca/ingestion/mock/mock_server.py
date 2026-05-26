"""FastAPI mock server — port 8001.

Endpoints:
  POST /mock/fill   → generate synthetic fill, publish to pb:fills stream
  POST /mock/order  → generate synthetic order, publish to pb:orders stream
  POST /mock/tick   → generate market tick, publish to pb:market_ticks stream
  GET  /mock/seed   → trigger full dlt batch seed (returns immediately; runs sync)
  GET  /mock/health → liveness probe
"""

from __future__ import annotations

import logging
import os
import random
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
from faker import Faker
from fastapi import Body, FastAPI, HTTPException

logger = logging.getLogger(__name__)
fake = Faker("en_GB")

_COUNTERPARTIES = ["CP_ABCD", "CP_EFGH", "CP_IJKL", "CP_MNOP", "CP_QRST"]
_TRADERS = [f"TRD-{i:03d}" for i in range(1, 11)]
_VENUES = ["XLON", "XETR", "XPAR", "XEUR", "BLTX"]
_INSTRUMENT_IDS = (
    [f"EQTY-{i:03d}" for i in range(1, 21)]
    + [f"FUTS-{i:03d}" for i in range(1, 11)]
    + [f"BOND-{i:03d}" for i in range(1, 11)]
    + [f"FXFW-{i:03d}" for i in range(1, 11)]
)

redis_client: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        redis_client = aioredis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info("Connected to Redis at %s", redis_url)
    except Exception as exc:
        logger.warning("Redis unavailable (%s) — stream publishing disabled", exc)
        redis_client = None
    yield
    if redis_client:
        await redis_client.aclose()


app = FastAPI(title="TCA Mock Server", version="1.0.0", lifespan=lifespan)


def _to_stream_dict(data: dict[str, Any]) -> dict[str, str]:
    return {k: str(v) for k, v in data.items() if v is not None}


async def _publish(stream: str, data: dict[str, Any]) -> str | None:
    if redis_client is None:
        return None
    try:
        entry_id = await redis_client.xadd(stream, _to_stream_dict(data))
        return entry_id
    except Exception as exc:
        logger.error("Failed to publish to %s: %s", stream, exc)
        return None


def _gen_fill(overrides: dict | None = None) -> dict[str, Any]:
    instrument_id = random.choice(_INSTRUMENT_IDS)
    base_price = (
        50.0
        if instrument_id.startswith("EQTY")
        else (
            4000.0
            if instrument_id.startswith("FUTS")
            else (100.0 if instrument_id.startswith("BOND") else 1.10)
        )
    )
    fill_price = round(base_price * random.uniform(0.98, 1.02), 6)
    data: dict[str, Any] = {
        "fill_id": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "instrument_id": instrument_id,
        "instrument_class": instrument_id[:4]
        .lower()
        .replace("eqty", "equity")
        .replace("futs", "equity_future")
        .replace("bond", "fixed_income")
        .replace("fxfw", "fx_derivative"),
        "counterparty_id": random.choice(_COUNTERPARTIES),
        "side": random.choice(["BUY", "SELL"]),
        "fill_price": fill_price,
        "fill_quantity": random.randint(100, 10_000),
        "venue_id": random.choice(_VENUES),
        "fill_time": datetime.now(tz=timezone.utc).isoformat(),
        "market_impact_bps": round(random.uniform(0.1, 20.0), 4),
        "commission_bps": round(random.uniform(0.5, 3.0), 2),
        "currency": "EUR",
    }
    if overrides:
        data.update(overrides)
    return data


def _gen_order(overrides: dict | None = None) -> dict[str, Any]:
    instrument_id = random.choice(_INSTRUMENT_IDS)
    data: dict[str, Any] = {
        "order_id": str(uuid.uuid4()),
        "instrument_id": instrument_id,
        "counterparty_id": random.choice(_COUNTERPARTIES),
        "trader_id": random.choice(_TRADERS),
        "side": random.choice(["BUY", "SELL"]),
        "order_type": random.choice(["MARKET", "VWAP", "TWAP", "IS"]),
        "quantity": random.randint(1_000, 100_000),
        "arrival_price": round(random.uniform(10.0, 200.0), 6),
        "order_time": datetime.now(tz=timezone.utc).isoformat(),
        "venue_id": random.choice(_VENUES),
        "currency": "EUR",
        "status": "NEW",
    }
    if overrides:
        data.update(overrides)
    return data


def _gen_tick(overrides: dict | None = None) -> dict[str, Any]:
    instrument_id = random.choice(_INSTRUMENT_IDS)
    data: dict[str, Any] = {
        "tick_id": str(uuid.uuid4()),
        "instrument_id": instrument_id,
        "bid": round(random.uniform(10.0, 200.0), 6),
        "ask": round(random.uniform(10.01, 200.1), 6),
        "last": round(random.uniform(10.0, 200.0), 6),
        "volume": random.randint(100, 50_000),
        "venue_id": random.choice(_VENUES),
        "ts": datetime.now(tz=timezone.utc).isoformat(),
    }
    if overrides:
        data.update(overrides)
    return data


@app.get("/mock/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "redis": "connected" if redis_client else "disconnected"}


@app.post("/mock/fill")
async def post_fill(
    payload: dict | None = Body(
        default=None,
        openapi_examples={
            "equity": {
                "summary": "Cash equity fill — BUY on XLON",
                "value": {
                    "instrument_id": "EQTY-001",
                    "instrument_class": "equity",
                    "side": "BUY",
                    "fill_price": 51.23,
                    "fill_quantity": 5000,
                    "venue_id": "XLON",
                    "counterparty_id": "CP_ABCD",
                    "market_impact_bps": 4.75,
                    "commission_bps": 1.20,
                    "currency": "EUR",
                },
            },
            "fixed_income": {
                "summary": "Fixed income fill — SELL on XEUR",
                "value": {
                    "instrument_id": "BOND-003",
                    "instrument_class": "fixed_income",
                    "side": "SELL",
                    "fill_price": 99.75,
                    "fill_quantity": 1000,
                    "venue_id": "XEUR",
                    "counterparty_id": "CP_EFGH",
                    "market_impact_bps": 2.10,
                    "commission_bps": 0.80,
                    "currency": "EUR",
                },
            },
            "random": {
                "summary": "Fully random (no overrides)",
                "value": {},
            },
        },
    ),
) -> dict[str, Any]:
    fill = _gen_fill(payload)
    entry_id = await _publish("pb:fills", fill)
    return {**fill, "_stream_entry_id": entry_id}


@app.post("/mock/order")
async def post_order(
    payload: dict | None = Body(
        default=None,
        openapi_examples={
            "vwap_equity": {
                "summary": "Equity VWAP order — BUY on XLON",
                "value": {
                    "instrument_id": "EQTY-005",
                    "counterparty_id": "CP_ABCD",
                    "trader_id": "TRD-002",
                    "side": "BUY",
                    "order_type": "VWAP",
                    "quantity": 25000,
                    "arrival_price": 48.90,
                    "venue_id": "XLON",
                    "currency": "EUR",
                },
            },
            "is_future": {
                "summary": "Equity future IS order — SELL on XEUR",
                "value": {
                    "instrument_id": "FUTS-002",
                    "counterparty_id": "CP_IJKL",
                    "trader_id": "TRD-007",
                    "side": "SELL",
                    "order_type": "IS",
                    "quantity": 10000,
                    "arrival_price": 4010.50,
                    "venue_id": "XEUR",
                    "currency": "EUR",
                },
            },
            "random": {
                "summary": "Fully random (no overrides)",
                "value": {},
            },
        },
    ),
) -> dict[str, Any]:
    order = _gen_order(payload)
    entry_id = await _publish("pb:orders", order)
    return {**order, "_stream_entry_id": entry_id}


@app.post("/mock/tick")
async def post_tick(
    payload: dict | None = Body(
        default=None,
        openapi_examples={
            "equity_tick": {
                "summary": "Equity tick — XLON",
                "value": {
                    "instrument_id": "EQTY-001",
                    "bid": 51.10,
                    "ask": 51.15,
                    "last": 51.12,
                    "volume": 12500,
                    "venue_id": "XLON",
                },
            },
            "fx_derivative_tick": {
                "summary": "FX derivative tick — XEUR",
                "value": {
                    "instrument_id": "FXFW-001",
                    "bid": 1.0842,
                    "ask": 1.0845,
                    "last": 1.0843,
                    "volume": 5000000,
                    "venue_id": "XEUR",
                },
            },
            "random": {
                "summary": "Fully random (no overrides)",
                "value": {},
            },
        },
    ),
) -> dict[str, Any]:
    tick = _gen_tick(payload)
    entry_id = await _publish("pb:market_ticks", tick)
    return {**tick, "_stream_entry_id": entry_id}


@app.get("/mock/seed")
async def seed_endpoint() -> dict[str, Any]:
    try:
        from ingestion.seed import seed_all

        results = seed_all()
        return {"status": "ok", "pipelines": results}
    except Exception as exc:
        logger.exception("Seed failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
