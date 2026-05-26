"""MiFID II RTS 27/28 export — daily CSV for regulatory submission."""

from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path

import sqlalchemy as sa

_OUTPUT_DIR = Path(os.environ.get("REPORT_OUTPUT_DIR", "/tmp/tca_reports"))
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_SQL = """
SELECT
    f.hub_order_key                                                  AS order_id,
    f.instrument_id,
    f.instrument_class,
    f.side,
    f.quantity                                                       AS total_quantity,
    ROUND((f.filled_quantity * f.avg_fill_price)::numeric, 2)       AS notional_eur,
    f.venue_id,
    f.pre_trade_waiver_type                                          AS waiver_type,
    (f.post_trade_deferral_type IS NOT NULL)                        AS is_lrgs_deferral,
    CASE f.instrument_class
        WHEN 'equity'          THEN 'EQUITY'
        WHEN 'equity_future'   THEN 'EQUITY_DERIV'
        WHEN 'fixed_income'    THEN 'BOND'
        WHEN 'fx_derivative'   THEN 'FX'
        ELSE 'OTHER'
    END                                                              AS rts27_category,
    f.arrival_price,
    f.avg_fill_price,
    f.arrival_slippage_bps,
    f.counterparty_id,
    m.booking_entity                                                 AS legal_entity,
    f.trade_date
FROM mart_trading_risk.fact_order_execution AS f
LEFT JOIN mart_trading_risk.dim_mifid AS m USING (hub_order_key)
WHERE f.trade_date = :d
"""


def _engine() -> sa.Engine:
    return sa.create_engine(os.environ["DATABASE_URL"])


def generate_mifid_rts27(trade_date: date, counterparty_id: str | None = None) -> Path:
    """Generate RTS 27 export CSV for a given trade date.

    If counterparty_id is provided, restricts to that counterparty (CLIENT role).
    Without counterparty_id returns all rows (COMPLIANCE / ADMIN).
    """
    sql_str = _SQL
    params: dict[str, object] = {"d": trade_date}
    if counterparty_id:
        sql_str += " AND f.counterparty_id = :cp"
        params["cp"] = counterparty_id

    sql_str += " ORDER BY f.hub_order_key"

    suffix = f"_{counterparty_id}" if counterparty_id else ""
    out = _OUTPUT_DIR / f"mifid_rts27_{trade_date}{suffix}.csv"

    with _engine().connect() as conn:
        result = conn.execute(sa.text(sql_str), params)
        rows = result.fetchall()
        keys = list(result.keys())

    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(keys)
        writer.writerows(rows)

    return out
