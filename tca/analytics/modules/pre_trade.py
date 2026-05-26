from __future__ import annotations

import numpy as np
import pandas as pd

_ETA = 0.1
_VOL_BY_CLASS = {
    "equity": 0.015,
    "equity_future": 0.012,
    "fixed_income": 0.003,
    "fx_derivative": 0.005,
}
_ADV_BY_CLASS = {
    "equity": 2_000_000,
    "equity_future": 50_000,
    "fixed_income": 50_000_000,
    "fx_derivative": 100_000_000,
}


class PreTrade:
    """Pre-trade analytics: estimates expected market impact and optimal execution horizon.

    Uses the Almgren-Chriss framework to estimate:
    - Expected market impact given quantity and ADV
    - Optimal VWAP participation rate
    - Risk-adjusted execution horizon
    """

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
        df["participation_rate"] = (
            (df["quantity"] / df["adv"].replace(0, float("nan")))
            .clip(upper=1.0)
            .round(4)
        )

        # Expected market impact estimate (pre-trade)
        df["est_impact_bps"] = (
            _ETA * df["vol_daily"] * np.sqrt(df["participation_rate"]) * 10_000
        ).round(4)

        # Optimal execution horizon (minutes): sqrt(X/ADV) × session_duration_min
        session_minutes = 510  # 8.5 hours
        df["optimal_horizon_min"] = (
            (np.sqrt(df["participation_rate"]) * session_minutes)
            .round(0)
            .clip(lower=5, upper=session_minutes)
        )

        # Recommend algo based on participation rate
        df["recommended_algo"] = df["participation_rate"].apply(
            lambda p: "IS" if p < 0.01 else ("VWAP" if p < 0.05 else "POV")
        )

        cols = [
            "hub_order_key",
            "instrument_class",
            "side",
            "quantity",
            "arrival_price",
            "participation_rate",
            "est_impact_bps",
            "optimal_horizon_min",
            "recommended_algo",
            "counterparty_id",
            "trade_date",
        ]
        return df[[c for c in cols if c in df.columns]]
