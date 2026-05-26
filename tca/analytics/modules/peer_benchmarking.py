from __future__ import annotations

import pandas as pd


class PeerBenchmarking:
    """Compares execution quality against VWAP, TWAP, arrival, and close benchmarks.

    Produces a league-table of algo performance vs benchmarks.
    Used for MiFID II RTS 28 top-5 venue and algo reporting.
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

        if not benchmarks.empty:
            bm = benchmarks[
                ["instrument_id", "session_vwap", "session_twap", "session_close"]
            ].copy()
            df = df.merge(bm, on="instrument_id", how="left")
        else:
            df["session_vwap"] = df.get("avg_fill_price", df.get("arrival_price"))
            df["session_twap"] = df["session_vwap"]
            df["session_close"] = df["session_vwap"]

        side = df["side"].map({"BUY": 1, "SELL": -1}).fillna(1)
        p = df["avg_fill_price"].fillna(df["arrival_price"])

        def _slippage(benchmark_col: str) -> pd.Series:
            bm_price = df[benchmark_col].fillna(p)
            return (
                (p - bm_price) / bm_price.replace(0, float("nan")) * 10_000 * side
            ).round(4)

        df["vwap_slippage_bps"] = _slippage("session_vwap")
        df["twap_slippage_bps"] = _slippage("session_twap")
        df["close_slippage_bps"] = _slippage("session_close")
        df["arrival_slippage_bps"] = (
            (p - df["arrival_price"])
            / df["arrival_price"].replace(0, float("nan"))
            * 10_000
            * side
        ).round(4)

        # Algo-level aggregate
        algo_perf = (
            df.groupby(["algo_id", "instrument_class"])
            .agg(
                order_count=("hub_order_key", "count"),
                avg_vwap_slippage=("vwap_slippage_bps", "mean"),
                avg_twap_slippage=("twap_slippage_bps", "mean"),
                avg_arrival_slippage=("arrival_slippage_bps", "mean"),
                avg_close_slippage=("close_slippage_bps", "mean"),
            )
            .round(4)
            .reset_index()
        )
        algo_perf["algo_rank"] = (
            algo_perf.groupby("instrument_class")["avg_vwap_slippage"].rank(
                method="min", ascending=True
            )
        ).astype(int)

        return algo_perf
