from __future__ import annotations

import numpy as np
import pandas as pd

# Almgren-Chriss market impact parameters
_ETA = 0.1        # permanent impact coefficient
_GAMMA = 0.314    # temporary impact coefficient
_VOL_BY_CLASS: dict[str, float] = {
    "equity":        0.015,
    "equity_future": 0.012,
    "fixed_income":  0.003,
    "fx_derivative": 0.005,
}
_ADV_BY_CLASS: dict[str, int] = {
    "equity":        2_000_000,
    "equity_future": 50_000,
    "fixed_income":  50_000_000,
    "fx_derivative": 100_000_000,
}


def almgren_chriss_impact(
    quantity: float, adv: float, vol_daily: float, side: str
) -> float:
    """Almgren-Chriss permanent market impact in basis points."""
    participation = quantity / max(adv, 1)
    impact = _ETA * vol_daily * np.sqrt(participation) * 10_000
    return round(float(impact), 4)


class CostDecomposition:
    """Decomposes total execution cost into arrival slippage, market impact,
    commission, and timing components using the Almgren-Chriss model."""

    def run(
        self,
        orders: pd.DataFrame,
        fills: pd.DataFrame,
        benchmarks: pd.DataFrame,
    ) -> pd.DataFrame:
        if orders.empty:
            return pd.DataFrame()

        df = orders.copy()

        df["vol_daily"] = df["instrument_class"].map(_VOL_BY_CLASS).fillna(0.01)
        df["adv"] = df["instrument_class"].map(_ADV_BY_CLASS).fillna(1_000_000)

        # Almgren-Chriss estimated impact
        df["ac_impact_bps"] = df.apply(
            lambda r: almgren_chriss_impact(
                r.get("quantity", 0),
                r["adv"],
                r["vol_daily"],
                r.get("side", "BUY"),
            ),
            axis=1,
        )

        # Arrival slippage from enriched orders
        df["side_factor"] = df["side"].map({"BUY": 1, "SELL": -1}).fillna(1)
        df["arrival_slippage_bps"] = (
            (df["avg_fill_price"] - df["arrival_price"])
            / df["arrival_price"].replace(0, np.nan)
            * 10_000
            * df["side_factor"]
        ).round(4)

        # Spread cost (half bid-ask, approximated from instrument class)
        _spread_bps = {"equity": 2.0, "equity_future": 0.5, "fixed_income": 5.0, "fx_derivative": 3.0}
        df["spread_cost_bps"] = df["instrument_class"].map(_spread_bps).fillna(2.0)

        df["total_cost_bps"] = (
            df["arrival_slippage_bps"].fillna(0)
            + df.get("avg_market_impact_bps", pd.Series(0, index=df.index)).fillna(0)
            + df.get("avg_commission_bps", pd.Series(0, index=df.index)).fillna(0)
            + df["spread_cost_bps"]
        ).round(4)

        cols = [
            "hub_order_key", "instrument_class", "side", "quantity",
            "arrival_price", "avg_fill_price",
            "arrival_slippage_bps", "ac_impact_bps", "spread_cost_bps",
            "total_cost_bps", "counterparty_id", "trader_id", "trade_date",
        ]
        return df[[c for c in cols if c in df.columns]]
