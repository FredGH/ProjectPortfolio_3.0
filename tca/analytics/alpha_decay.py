from __future__ import annotations

import pandas as pd

# Volatility regime thresholds (annualised bps)
_REGIME_LOW = 80.0
_REGIME_HIGH = 150.0


def classify_regime(daily_vol_annualized_bps: float) -> str:
    if daily_vol_annualized_bps < _REGIME_LOW:
        return "LOW"
    if daily_vol_annualized_bps < _REGIME_HIGH:
        return "MEDIUM"
    return "HIGH"


def compute_curves(orders: pd.DataFrame, benchmarks: pd.DataFrame) -> pd.DataFrame:
    """Compute regime-tagged alpha decay curves.

    Alpha = post-trade price continuation in direction of trade.
    Positive alpha = trade was directionally correct.

    Returns one row per (vol_regime, instrument_class) with average alpha
    at t+30m, t+1h, t+4h, t+close.
    """
    if orders.empty:
        return pd.DataFrame()

    df = orders.copy()

    # Assign vol regime from benchmark intraday volatility
    if not benchmarks.empty and "daily_vol_annualized" in benchmarks.columns:
        bm = benchmarks[["instrument_id", "daily_vol_annualized"]].copy()
        bm["daily_vol_annualized_bps"] = bm["daily_vol_annualized"] * 10_000
        df = df.merge(bm, on="instrument_id", how="left")
        df["vol_regime"] = df["daily_vol_annualized_bps"].fillna(100).apply(classify_regime)
    elif "vol_regime" in df.columns:
        pass
    else:
        df["vol_regime"] = "MEDIUM"

    alpha_cols = ["alpha_t30m_bps", "alpha_t1h_bps", "alpha_t4h_bps", "alpha_close_bps"]
    available = [c for c in alpha_cols if c in df.columns]

    if not available:
        return pd.DataFrame(
            {"vol_regime": [], "instrument_class": [], "msg": ["No alpha columns available"]}
        )

    group_cols = ["vol_regime", "instrument_class"]
    agg_dict = {col: "mean" for col in available}
    agg_dict["hub_order_key"] = "count"

    curve = (
        df.groupby(group_cols)
        .agg(agg_dict)
        .rename(columns={"hub_order_key": "order_count"})
        .round(4)
        .reset_index()
    )

    # Alpha decay rate: how fast alpha decays from t30m to close
    if "alpha_t30m_bps" in curve.columns and "alpha_close_bps" in curve.columns:
        curve["alpha_decay_rate"] = (
            (curve["alpha_t30m_bps"] - curve["alpha_close_bps"])
            / curve["alpha_t30m_bps"].replace(0, float("nan"))
        ).round(4)

    return curve
