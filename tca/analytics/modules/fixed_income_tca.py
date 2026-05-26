from __future__ import annotations

import pandas as pd


class FixedIncomeTCA:
    """Fixed income TCA: DV01-adjusted slippage, yield slippage, duration risk.

    Bond TCA measures cost in yield space (bps of yield) and price space.
    DV01-adjusted notional normalises across different durations.
    """

    def run(
        self,
        orders: pd.DataFrame,
        fills: pd.DataFrame,
        benchmarks: pd.DataFrame,
    ) -> pd.DataFrame:
        if orders.empty:
            return pd.DataFrame()

        bonds = orders[orders["instrument_class"] == "fixed_income"].copy()
        if bonds.empty:
            return pd.DataFrame()

        side = bonds["side"].map({"BUY": 1, "SELL": -1}).fillna(1)
        fill_p = bonds["avg_fill_price"].fillna(bonds["arrival_price"])

        # Price slippage (bonds trade as % of par)
        bonds["price_slippage_bps"] = (
            (fill_p - bonds["arrival_price"])
            / bonds["arrival_price"].replace(0, float("nan"))
            * 10_000
            * side
        ).round(4)

        # Yield slippage: ΔY ≈ ΔP / DV01 (approximate, DV01 not in orders table)
        # Use duration proxy: 0.07 bps yield per bps price for 5yr bond
        bonds["yield_slippage_bps"] = (bonds["price_slippage_bps"] * 0.07).round(4)

        # DV01-adjusted notional (approx: 5yr duration × quantity × price / 100 / 10000)
        bonds["dv01_adjusted_notional"] = (
            bonds.get("quantity", pd.Series(1_000_000, index=bonds.index))
            * fill_p
            / 100
            * 5
            / 10_000
        ).round(2)

        cols = [
            "hub_order_key",
            "instrument_id",
            "side",
            "quantity",
            "arrival_price",
            "avg_fill_price",
            "price_slippage_bps",
            "yield_slippage_bps",
            "dv01_adjusted_notional",
            "counterparty_id",
            "trader_id",
            "trade_date",
        ]
        return bonds[[c for c in cols if c in bonds.columns]]
