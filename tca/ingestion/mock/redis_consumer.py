"""Redis Streams consumer — writes real-time fills to stg_raw.rt_fills.

Reads from pb:fills using XREADGROUP (consumer group per stream).
Run as a standalone process or via Airflow dag_rt_consumer.py.

Usage:
    python -m ingestion.mock.redis_consumer
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import redis
import sqlalchemy as sa
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_STREAM_FILLS = "pb:fills"
_GROUP_FILLS = "tca-fills-cg"
_CONSUMER_NAME = "rt-consumer-1"
_BLOCK_MS = 2_000
_BATCH_SIZE = 100

_INSERT_RT_FILL = sa.text(
    """
    INSERT INTO stg_raw.rt_fills (
        stream_id, fill_id, order_id, instrument_id, instrument_class,
        counterparty_id, side, fill_price, fill_quantity, venue_id,
        fill_time, market_impact_bps, commission_bps, currency, received_at
    ) VALUES (
        :stream_id, :fill_id, :order_id, :instrument_id, :instrument_class,
        :counterparty_id, :side, :fill_price, :fill_quantity, :venue_id,
        :fill_time, :market_impact_bps, :commission_bps, :currency, :received_at
    )
    ON CONFLICT (stream_id) DO NOTHING
"""
)


def _parse_fill(stream_id: str, data: dict[bytes | str, bytes | str]) -> dict:
    def _s(k: str) -> str | None:
        v = data.get(k) or data.get(k.encode())
        return v.decode() if isinstance(v, bytes) else v

    def _f(k: str) -> float | None:
        v = _s(k)
        return float(v) if v is not None else None

    def _i(k: str) -> int | None:
        v = _s(k)
        return int(v) if v is not None else None

    fill_time_raw = _s("fill_time")
    fill_time = (
        datetime.fromisoformat(fill_time_raw)
        if fill_time_raw
        else datetime.now(tz=timezone.utc)
    )

    return {
        "stream_id": stream_id,
        "fill_id": _s("fill_id"),
        "order_id": _s("order_id"),
        "instrument_id": _s("instrument_id"),
        "instrument_class": _s("instrument_class"),
        "counterparty_id": _s("counterparty_id"),
        "side": _s("side"),
        "fill_price": _f("fill_price"),
        "fill_quantity": _i("fill_quantity"),
        "venue_id": _s("venue_id"),
        "fill_time": fill_time,
        "market_impact_bps": _f("market_impact_bps"),
        "commission_bps": _f("commission_bps"),
        "currency": _s("currency"),
        "received_at": datetime.now(tz=timezone.utc),
    }


def _ensure_consumer_group(r: redis.Redis) -> None:
    try:
        r.xgroup_create(_STREAM_FILLS, _GROUP_FILLS, id="0", mkstream=True)
        logger.info("Created consumer group %s on %s", _GROUP_FILLS, _STREAM_FILLS)
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def consume(poll_interval: float = 0.5) -> None:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    db_url = os.environ["DATABASE_URL"]

    r = redis.from_url(redis_url, decode_responses=False)
    engine = sa.create_engine(db_url)

    _ensure_consumer_group(r)

    logger.info("Redis consumer started — listening on %s", _STREAM_FILLS)

    while True:
        try:
            messages = r.xreadgroup(
                groupname=_GROUP_FILLS,
                consumername=_CONSUMER_NAME,
                streams={_STREAM_FILLS: ">"},
                count=_BATCH_SIZE,
                block=_BLOCK_MS,
            )
        except redis.exceptions.ConnectionError as exc:
            logger.error("Redis connection lost: %s — retrying in 5s", exc)
            time.sleep(5)
            continue

        if not messages:
            continue

        for _stream, entries in messages:
            rows = []
            entry_ids = []
            for entry_id, data in entries:
                sid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                try:
                    rows.append(_parse_fill(sid, data))
                    entry_ids.append(entry_id)
                except Exception as exc:
                    logger.warning("Skipping malformed fill entry %s: %s", sid, exc)

            if rows:
                try:
                    with engine.begin() as conn:
                        conn.execute(_INSERT_RT_FILL, rows)
                    r.xack(_STREAM_FILLS, _GROUP_FILLS, *entry_ids)
                    logger.debug("Persisted %d fills", len(rows))
                except Exception as exc:
                    logger.error("Failed to persist fills batch: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    consume()
