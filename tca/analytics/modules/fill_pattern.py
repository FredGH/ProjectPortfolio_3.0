from __future__ import annotations

import pandas as pd


class FillPattern:
    """Fill distribution analysis: timing, size distribution, fill rate.

    Detects abnormal fill patterns that may indicate algo miscalibration
    or unusual market conditions.
    """

    def run(
        self,
        orders: pd.DataFrame,
        fills: pd.DataFrame,
        benchmarks: pd.DataFrame,
    ) -> pd.DataFrame:
        if fills.empty or orders.empty:
            return pd.DataFrame()

        fills_c = fills.copy()
        orders_c = orders[
            [
                "hub_order_key",
                "order_id",
                "quantity",
                "order_time",
                "algo_id",
                "instrument_class",
            ]
        ].copy()

        # Merge order metadata into fills
        merged = fills_c.merge(
            orders_c.rename(columns={"hub_order_key": "_ok"}),
            on="order_id",
            how="left",
        )

        # Fill timing: seconds from order placement to each fill
        if "order_time" in merged.columns and "fill_time" in merged.columns:
            merged["seconds_to_fill"] = (
                (
                    pd.to_datetime(merged["fill_time"])
                    - pd.to_datetime(merged["order_time"])
                )
                .dt.total_seconds()
                .clip(lower=0)
            )
        else:
            merged["seconds_to_fill"] = float("nan")

        # Fill size as % of order quantity
        merged["fill_size_pct"] = (
            merged["fill_quantity"] / merged["quantity"].replace(0, float("nan")) * 100
        ).round(2)

        agg = (
            merged.groupby("order_id")
            .agg(
                fill_count=("fill_id", "count"),
                avg_fill_size_pct=("fill_size_pct", "mean"),
                std_fill_size_pct=("fill_size_pct", "std"),
                avg_seconds_to_fill=("seconds_to_fill", "mean"),
                max_seconds_to_fill=("seconds_to_fill", "max"),
                total_filled_qty=("fill_quantity", "sum"),
            )
            .round(4)
            .reset_index()
        )

        # Flag fragmented orders (>4 fills)
        agg["is_fragmented"] = agg["fill_count"] > 4

        return agg
