from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterator

import dlt
import numpy as np

# Futures — FUTS-001…FUTS-010 (must match ref_data_source.py)
_FUTURES: list[dict] = [
    {"instrument_id": "FUTS-001", "underlying": "EURO STOXX 50", "mid_price": 4_850.0, "tick_size": 1.0},
    {"instrument_id": "FUTS-002", "underlying": "DAX",           "mid_price": 16_500.0,"tick_size": 0.5},
    {"instrument_id": "FUTS-003", "underlying": "FTSE 100",      "mid_price": 7_600.0, "tick_size": 0.5},
    {"instrument_id": "FUTS-004", "underlying": "CAC 40",        "mid_price": 7_200.0, "tick_size": 0.5},
    {"instrument_id": "FUTS-005", "underlying": "AEX",           "mid_price": 870.0,   "tick_size": 0.05},
    {"instrument_id": "FUTS-006", "underlying": "SMI",           "mid_price": 11_500.0,"tick_size": 1.0},
    {"instrument_id": "FUTS-007", "underlying": "IBEX 35",       "mid_price": 10_800.0,"tick_size": 1.0},
    {"instrument_id": "FUTS-008", "underlying": "OMXS30",        "mid_price": 2_350.0, "tick_size": 0.25},
    {"instrument_id": "FUTS-009", "underlying": "BEL 20",        "mid_price": 3_850.0, "tick_size": 0.5},
    {"instrument_id": "FUTS-010", "underlying": "ATX",           "mid_price": 3_600.0, "tick_size": 0.5},
]

# Quarterly expiry: last Friday of March 2025 = 2025-03-21
_EXPIRY_DATE = "2025-03-21"
_SETTLEMENT_DATE = "2025-03-24"  # T+3


def _round_to_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 6)


@dlt.source(name="eurex")
def eurex_source(trade_date: date | None = None) -> Iterator:
    if trade_date is None:
        trade_date = date(2025, 1, 15)

    rng = np.random.default_rng(seed=7)
    loaded_at = datetime.now(tz=timezone.utc)

    @dlt.resource(
        name="edsp_settlements",
        write_disposition="merge",
        primary_key=["instrument_id", "expiry_date"],
    )
    def edsp_settlements() -> Iterator[dict]:
        for fut in _FUTURES:
            # EDSP = mid_price ± small intraday noise (±0.3%)
            noise_pct = float(rng.normal(0, 0.003))
            edsp = _round_to_tick(fut["mid_price"] * (1 + noise_pct), fut["tick_size"])
            prev_settlement = _round_to_tick(fut["mid_price"] * float(rng.uniform(0.995, 1.005)), fut["tick_size"])
            daily_pnl_per_contract = round((edsp - prev_settlement) * 10, 2)  # contract_size=10

            yield {
                "instrument_id": fut["instrument_id"],
                "underlying_name": fut["underlying"],
                "trade_date": trade_date.isoformat(),
                "expiry_date": _EXPIRY_DATE,
                "settlement_date": _SETTLEMENT_DATE,
                "edsp": edsp,
                "prev_settlement": prev_settlement,
                "daily_pnl_per_contract": daily_pnl_per_contract,
                "tick_size": fut["tick_size"],
                "contract_size": 10,
                "currency": "EUR",
                "venue_id": "XEUR",
                "_loaded_at": loaded_at,
            }

    return (edsp_settlements,)
