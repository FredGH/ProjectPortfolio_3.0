from __future__ import annotations

import pandas as pd


class AdverseSelection:
    """Measures informational disadvantage: did prices move against us around fills?

    Positive adverse_selection_bps means counterparty had better information
    (bought before price rose / sold before price fell).
    """

    def run(
        self,
        orders: pd.DataFrame,
        fills: pd.DataFrame,
        benchmarks: pd.DataFrame,
    ) -> pd.DataFrame:
        if fills.empty:
            return pd.DataFrame()

        df = fills.copy()

        # Adverse selection proxy: distance between fill_price and session VWAP
        if not benchmarks.empty and "session_vwap" in benchmarks.columns:
            bm = benchmarks[["instrument_id", "session_vwap"]].copy()
            df = df.merge(bm, on="instrument_id", how="left")
        else:
            df["session_vwap"] = df["fill_price"]

        df["side_factor"] = df["side"].map({"BUY": 1, "SELL": -1}).fillna(1)

        # BUY adverse selection: fill_price > session_vwap (paid above market)
        # SELL adverse selection: fill_price < session_vwap (received below market)
        df["adverse_selection_bps"] = (
            (df["fill_price"] - df["session_vwap"].fillna(df["fill_price"]))
            / df["fill_price"].replace(0, float("nan"))
            * 10_000
            * df["side_factor"]
        ).round(4)

        # Flag significant adverse selection (>10 bps)
        df["is_adversely_selected"] = df["adverse_selection_bps"] > 10.0

        order_agg = (
            df.groupby("order_id")
            .agg(
                avg_adverse_selection_bps=("adverse_selection_bps", "mean"),
                max_adverse_selection_bps=("adverse_selection_bps", "max"),
                adversely_selected_fills=("is_adversely_selected", "sum"),
                fill_count=("fill_id", "count"),
            )
            .round(4)
            .reset_index()
        )
        order_agg["adverse_selection_rate"] = (
            order_agg["adversely_selected_fills"]
            / order_agg["fill_count"].replace(0, 1)
        ).round(4)

        return order_agg
