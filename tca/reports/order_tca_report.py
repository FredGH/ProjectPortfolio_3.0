"""Order TCA report — per-order HTML summary and venue scorecard CSV."""
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


def generate_order_tca_report(trade_date: date) -> Path:
    """Write a CSV of fact_order_execution for the given trade date."""
    sql = sa.text(
        "SELECT hub_order_key AS order_id, instrument_id, instrument_class, side, quantity AS total_quantity, "
        "avg_fill_price, arrival_price, arrival_slippage_bps, vwap_slippage_bps, "
        "market_impact_bps, timing_cost_bps, commission_bps, algo_id, venue_id, "
        "trader_id, vol_regime, counterparty_id, trade_date "
        "FROM mart_trading_risk.fact_order_execution "
        "WHERE trade_date = :d "
        "ORDER BY arrival_slippage_bps DESC"
    )
    with _engine().connect() as conn:
        df = pd.read_sql(sql, conn, params={"d": trade_date})

    out = _OUTPUT_DIR / f"order_tca_{trade_date}.csv"
    df.to_csv(out, index=False)
    return out


def generate_venue_scorecard(week_ending: date) -> Path:
    """Venue scorecard aggregated over the 5 trading days ending on week_ending."""
    week_start = week_ending - timedelta(days=4)
    sql = sa.text(
        "SELECT venue_id, instrument_class, "
        "COUNT(*) AS order_count, "
        "ROUND(AVG(vwap_slippage_bps)::numeric, 3) AS avg_vwap_slippage_bps, "
        "ROUND(AVG(market_impact_bps)::numeric, 3) AS avg_market_impact_bps, "
        "ROUND(AVG(arrival_slippage_bps)::numeric, 3) AS avg_arrival_slippage_bps "
        "FROM mart_trading_risk.fact_order_execution "
        "WHERE trade_date BETWEEN :s AND :e "
        "GROUP BY venue_id, instrument_class "
        "ORDER BY avg_vwap_slippage_bps"
    )
    with _engine().connect() as conn:
        df = pd.read_sql(sql, conn, params={"s": week_start, "e": week_ending})

    out = _OUTPUT_DIR / f"venue_scorecard_{week_ending}.csv"
    df.to_csv(out, index=False)
    return out
