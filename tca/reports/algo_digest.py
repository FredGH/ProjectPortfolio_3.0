"""Algo performance weekly digest — league table by instrument class."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

_OUTPUT_DIR = Path(os.environ.get("REPORT_OUTPUT_DIR", "/tmp/tca_reports"))
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _engine() -> sa.Engine:
    return sa.create_engine(os.environ["DATABASE_URL"])


def generate_algo_digest(week_ending: date) -> Path:
    """Algo league table for the week, written to CSV."""
    week_start = week_ending - timedelta(days=4)

    sql = sa.text(
        "SELECT algo_id, instrument_class, "
        "COUNT(*) AS order_count, "
        "ROUND(AVG(arrival_slippage_bps)::numeric, 3) AS avg_arrival_slippage_bps, "
        "ROUND(AVG(vwap_slippage_bps)::numeric, 3) AS avg_vwap_slippage_bps, "
        "ROUND(AVG(market_impact_bps)::numeric, 3) AS avg_market_impact_bps, "
        "ROUND(AVG(timing_cost_bps)::numeric, 3) AS avg_timing_cost_bps, "
        "ROUND(AVG(fill_rate_pct)::numeric, 4) AS avg_participation_rate "
        "FROM mart_trading_risk.fact_order_execution "
        "WHERE trade_date BETWEEN :s AND :e "
        "GROUP BY algo_id, instrument_class "
        "ORDER BY instrument_class, avg_arrival_slippage_bps"
    )

    with _engine().connect() as conn:
        df = pd.read_sql(sql, conn, params={"s": week_start, "e": week_ending})

    df["rank"] = (
        df.groupby("instrument_class")["avg_arrival_slippage_bps"]
        .rank(ascending=True, method="min")
        .astype(int)
    )

    out = _OUTPUT_DIR / f"algo_digest_{week_ending}.csv"
    df.to_csv(out, index=False)
    return out
