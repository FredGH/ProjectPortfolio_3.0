from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import dlt
import numpy as np

# Instrument prefixes matching ref_data_source.py
_INSTRUMENTS: list[tuple[str, float, float, int, int]] = (
    # (prefix, n, start_price, vol_daily, vol_low, vol_high) — one row per asset class
    [("EQTY", 20, 50.0, 0.015, 10_000, 2_000_000)]
    + [("FUTS", 10, 4_000.0, 0.012, 500, 50_000)]
    + [("BOND", 10, 100.0, 0.003, 1_000, 100_000)]
    + [("FXFW", 10, 1.10, 0.005, 1_000_000, 50_000_000)]
)

# European session: 07:00–15:30 UTC → 510 minutes → 1020 × 30-second bars
_SESSION_START_MINUTES = 7 * 60
_SESSION_END_MINUTES = 15 * 60 + 30
_N_BARS = (_SESSION_END_MINUTES - _SESSION_START_MINUTES) * 2  # 1020


def _generate_bars(
    trade_date: date,
    instrument_id: str,
    start_price: float,
    vol_daily: float,
    vol_low: int = 10_000,
    vol_high: int = 2_000_000,
    rng: np.random.Generator | None = None,
    instrument_class: str | None = None,
) -> list[dict]:
    if rng is None:
        rng = np.random.default_rng(seed=hash(instrument_id) % (2**32))
    n = _N_BARS
    sigma_bar = vol_daily / np.sqrt(n)

    # Vectorised GBM price path
    z = rng.standard_normal(n)
    log_returns = -0.5 * sigma_bar**2 + sigma_bar * z
    prices = start_price * np.exp(np.cumsum(log_returns))

    bar_start = datetime(
        trade_date.year,
        trade_date.month,
        trade_date.day,
        tzinfo=timezone.utc,
    ) + timedelta(minutes=_SESSION_START_MINUTES)

    bars = []
    prev_close = start_price
    for i in range(n):
        close = float(prices[i])
        open_ = prev_close
        noise = abs(float(rng.normal(0, sigma_bar * close)))
        high = round(max(open_, close) + noise, 6)
        low = round(min(open_, close) - noise, 6)
        volume = int(rng.integers(vol_low, vol_high))
        ts = bar_start + timedelta(seconds=30 * i)

        bars.append(
            {
                "bar_id": f"{instrument_id}_{int(ts.timestamp())}",
                "instrument_id": instrument_id,
                "bar_start": ts,
                "open": round(open_, 6),
                "high": high,
                "low": max(low, 0.0001),
                "close": round(close, 6),
                "volume": volume,
                "vwap": round((high + low + close) / 3, 6),
                "trade_date": trade_date.isoformat(),
                "_loaded_at": datetime.now(tz=timezone.utc),
            }
        )
        prev_close = close

    return bars


@dlt.source(name="market_data")
def market_data_source(trade_date: date | None = None) -> Iterator:
    if trade_date is None:
        trade_date = date(2025, 1, 15)

    @dlt.resource(
        name="tick_bars",
        write_disposition="merge",
        primary_key="bar_id",
    )
    def tick_bars() -> Iterator[dict]:
        for prefix, count, start_price, vol_daily, vol_low, vol_high in _INSTRUMENTS:
            for idx in range(1, count + 1):
                instrument_id = f"{prefix}-{idx:03d}"
                rng = np.random.default_rng(seed=hash(instrument_id) % (2**32))
                p0 = start_price * float(rng.uniform(0.85, 1.15))
                yield from _generate_bars(
                    trade_date, instrument_id, p0, vol_daily, vol_low, vol_high, rng
                )

    return (tick_bars,)
