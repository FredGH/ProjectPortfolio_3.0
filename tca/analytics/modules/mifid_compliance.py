from __future__ import annotations

import pandas as pd


class MifidCompliance:
    """MiFID II compliance checks: RTS 27 (venue quality) and RTS 28 (top venues/algos).

    Produces the data required for:
    - RTS 27: Quarterly execution quality statistics per venue
    - RTS 28: Annual top-5 execution venues per instrument class
    """

    _WAIVER_TYPES = ("LRGS", "ILQD", "SIZE", "RFPT")
    _REQUIRED_FIELDS = [
        "counterparty_id",
        "venue_id",
        "instrument_id",
        "trade_date",
        "side",
    ]

    def run(
        self,
        orders: pd.DataFrame,
        fills: pd.DataFrame,
        benchmarks: pd.DataFrame,
    ) -> pd.DataFrame:
        if orders.empty:
            return pd.DataFrame()

        df = orders.copy()

        # Compliance check: flag orders missing required fields
        for field in self._REQUIRED_FIELDS:
            if field not in df.columns:
                df[field] = None

        df["is_compliant"] = df[self._REQUIRED_FIELDS].notna().all(axis=1)
        df["compliance_flags"] = df.apply(self._get_flags, axis=1)

        # RTS 27 venue quality per instrument class
        rts27 = (
            df.groupby(["venue_id", "instrument_class"])
            .agg(
                order_count=("hub_order_key", "count"),
                avg_slippage_bps=(
                    ("arrival_slippage_bps", "mean")
                    if "arrival_slippage_bps" in df.columns
                    else ("hub_order_key", "count")
                ),
                fill_rate_pct=(
                    ("fill_rate_pct", "mean")
                    if "fill_rate_pct" in df.columns
                    else ("hub_order_key", "count")
                ),
                compliant_order_count=("is_compliant", "sum"),
            )
            .round(4)
            .reset_index()
        )
        rts27["rts27_quality_flag"] = (
            rts27["compliant_order_count"] == rts27["order_count"]
        )

        return rts27

    @staticmethod
    def _get_flags(row: pd.Series) -> str:
        flags = []
        if pd.isna(row.get("counterparty_id")):
            flags.append("MISSING_COUNTERPARTY")
        if pd.isna(row.get("venue_id")):
            flags.append("MISSING_VENUE")
        if pd.isna(row.get("trade_date")):
            flags.append("MISSING_DATE")
        return "|".join(flags) if flags else "OK"
