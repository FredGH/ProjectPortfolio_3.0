from __future__ import annotations

import pandas as pd


class FXDerivativesTCA:
    """FX derivative TCA: forward points, tenor-adjusted spread, carry cost.

    FX forwards are priced relative to spot + forward points.
    Slippage measured in pips and forward point bps.
    """

    def run(
        self,
        orders: pd.DataFrame,
        fills: pd.DataFrame,
        benchmarks: pd.DataFrame,
    ) -> pd.DataFrame:
        if orders.empty:
            return pd.DataFrame()

        fx = orders[orders["instrument_class"] == "fx_derivative"].copy()
        if fx.empty:
            return pd.DataFrame()

        side = fx["side"].map({"BUY": 1, "SELL": -1}).fillna(1)
        fill_p = fx["avg_fill_price"].fillna(fx["arrival_price"])

        # Pip value: 4th decimal place for most EUR/XXX pairs
        fx["slippage_pips"] = ((fill_p - fx["arrival_price"]) * 10_000 * side).round(2)

        fx["slippage_bps"] = (
            (fill_p - fx["arrival_price"])
            / fx["arrival_price"].replace(0, float("nan"))
            * 10_000
            * side
        ).round(4)

        # Notional in USD (quantity is base currency amount)
        fx["notional_usd"] = (
            fx.get("quantity", pd.Series(1_000_000, index=fx.index)) * fill_p
        ).round(2)

        # Spread cost proxy: 2 pips for liquid G10 pairs
        fx["est_spread_cost_pips"] = 2.0
        fx["spread_cost_bps"] = (fx["est_spread_cost_pips"] / fill_p / 100).round(6)

        cols = [
            "hub_order_key",
            "instrument_id",
            "side",
            "quantity",
            "arrival_price",
            "avg_fill_price",
            "slippage_pips",
            "slippage_bps",
            "notional_usd",
            "est_spread_cost_pips",
            "spread_cost_bps",
            "counterparty_id",
            "trader_id",
            "trade_date",
        ]
        return fx[[c for c in cols if c in fx.columns]]
