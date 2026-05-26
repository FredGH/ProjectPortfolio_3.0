from __future__ import annotations

import pandas as pd


class VenueSOR:
    """Smart Order Router venue performance scorecard.

    Ranks venues by average slippage, fill rate, and commission for each
    instrument class. Used for SOR calibration and RTS 27 reporting.
    """

    def run(
        self,
        orders: pd.DataFrame,
        fills: pd.DataFrame,
        benchmarks: pd.DataFrame,
    ) -> pd.DataFrame:
        if fills.empty:
            return pd.DataFrame()

        fills_aug = fills.copy()
        if not benchmarks.empty and "session_vwap" in benchmarks.columns:
            bm = benchmarks[["instrument_id", "session_vwap"]].copy()
            fills_aug = fills_aug.merge(bm, on="instrument_id", how="left")
        else:
            fills_aug["session_vwap"] = fills_aug["fill_price"]

        fills_aug["side_factor"] = fills_aug["side"].map({"BUY": 1, "SELL": -1}).fillna(1)
        fills_aug["slippage_vs_vwap_bps"] = (
            (fills_aug["fill_price"] - fills_aug["session_vwap"].fillna(fills_aug["fill_price"]))
            / fills_aug["fill_price"].replace(0, float("nan"))
            * 10_000
            * fills_aug["side_factor"]
        ).round(4)

        scorecard = (
            fills_aug.groupby(["venue_id", "instrument_class"])
            .agg(
                fill_count=("fill_id", "count"),
                total_volume=("fill_quantity", "sum"),
                avg_slippage_vs_vwap_bps=("slippage_vs_vwap_bps", "mean"),
                avg_commission_bps=("commission_bps", "mean"),
                avg_market_impact_bps=("market_impact_bps", "mean"),
            )
            .round(4)
            .reset_index()
        )

        scorecard["venue_rank"] = (
            scorecard.groupby("instrument_class")["avg_slippage_vs_vwap_bps"]
            .rank(method="min", ascending=True)
        ).astype(int)

        return scorecard.sort_values(["instrument_class", "venue_rank"])
