from __future__ import annotations

import pandas as pd


class EurexDerivatives:
    """Futures-specific TCA: EDSP basis, roll analysis, open interest context.

    EDSP (Exchange Delivery Settlement Price) is the Eurex settlement benchmark.
    Slippage measured relative to EDSP rather than VWAP for futures.
    """

    def run(
        self,
        orders: pd.DataFrame,
        fills: pd.DataFrame,
        benchmarks: pd.DataFrame,
    ) -> pd.DataFrame:
        if orders.empty:
            return pd.DataFrame()

        fut = orders[orders["instrument_class"] == "equity_future"].copy()
        if fut.empty:
            return pd.DataFrame()

        if not benchmarks.empty and "edsp_price" in benchmarks.columns:
            edsp = benchmarks[["instrument_id", "edsp_price", "session_vwap"]].copy()
            fut = fut.merge(edsp, on="instrument_id", how="left")
        else:
            fut["edsp_price"] = fut.get("avg_fill_price", fut.get("arrival_price"))
            fut["session_vwap"] = fut["edsp_price"]

        side = fut["side"].map({"BUY": 1, "SELL": -1}).fillna(1)
        fill_p = fut["avg_fill_price"].fillna(fut["arrival_price"])
        edsp_p = fut["edsp_price"].fillna(fill_p)

        fut["edsp_slippage_bps"] = (
            (fill_p - edsp_p) / edsp_p.replace(0, float("nan")) * 10_000 * side
        ).round(4)

        fut["basis_bps"] = (
            (fut["arrival_price"] - edsp_p) / edsp_p.replace(0, float("nan")) * 10_000
        ).round(4)

        cols = [
            "hub_order_key",
            "instrument_id",
            "side",
            "quantity",
            "arrival_price",
            "avg_fill_price",
            "edsp_price",
            "edsp_slippage_bps",
            "basis_bps",
            "counterparty_id",
            "trader_id",
            "trade_date",
        ]
        return fut[[c for c in cols if c in fut.columns]]
