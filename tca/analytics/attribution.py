from __future__ import annotations

import pandas as pd


def decompose(orders: pd.DataFrame, fills: pd.DataFrame) -> pd.DataFrame:
    """Decompose total execution cost into trader, algo, and market components.

    Cost attribution model:
      total_cost = trader_cost + algo_cost + market_cost
      - market_cost: unavoidable impact (Almgren-Chriss baseline)
      - algo_cost: slippage attributable to algo choice (vs VWAP)
      - trader_cost: residual (timing, venue selection, etc.)
    """
    if orders.empty:
        return pd.DataFrame()

    df = orders.copy()

    if "arrival_slippage_bps" not in df.columns:
        df["arrival_slippage_bps"] = 0.0
    if "avg_market_impact_bps" not in df.columns:
        df["avg_market_impact_bps"] = 0.0
    if "avg_commission_bps" not in df.columns:
        df["avg_commission_bps"] = 0.0

    # Market cost: baseline Almgren-Chriss (pre-computed in orders)
    df["market_cost_bps"] = df["avg_market_impact_bps"].fillna(0)

    # Algo cost: residual after market impact removed
    df["algo_cost_bps"] = (
        df["arrival_slippage_bps"].fillna(0) - df["market_cost_bps"]
    ).clip(lower=0)

    # Trader cost: commission + timing (everything not explained by algo or market)
    df["trader_cost_bps"] = df["avg_commission_bps"].fillna(0) + (
        df["arrival_slippage_bps"].fillna(0)
        - df["algo_cost_bps"]
        - df["market_cost_bps"]
    ).clip(lower=0)

    result = (
        df.groupby(["trader_id", "algo_id", "instrument_class"])
        .agg(
            order_count=("hub_order_key", "count"),
            avg_market_cost_bps=("market_cost_bps", "mean"),
            avg_algo_cost_bps=("algo_cost_bps", "mean"),
            avg_trader_cost_bps=("trader_cost_bps", "mean"),
            avg_total_cost_bps=("arrival_slippage_bps", "mean"),
        )
        .round(4)
        .reset_index()
    )

    result["pct_market"] = (
        result["avg_market_cost_bps"]
        / result["avg_total_cost_bps"].replace(0, float("nan"))
        * 100
    ).round(1)
    result["pct_algo"] = (
        result["avg_algo_cost_bps"]
        / result["avg_total_cost_bps"].replace(0, float("nan"))
        * 100
    ).round(1)
    result["pct_trader"] = (
        100 - result["pct_market"].fillna(0) - result["pct_algo"].fillna(0)
    ).round(1)

    return result
