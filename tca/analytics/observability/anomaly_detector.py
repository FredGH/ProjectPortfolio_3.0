from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
import sqlalchemy as sa

if TYPE_CHECKING:
    from analytics.observability.quarantine import Quarantine

logger = logging.getLogger(__name__)


# For a normal distribution, the percentage of data within each threshold is:
# Z-score threshold |    Data within |   Data outside (both tails)
# ±1σ	                68.3%	         31.7%
# ±2σ	                95.4%	        4.6%
# ±3σ	                99.7%	        0.3%
# ±4σ	                99.994%	        0.006%
# So at |Z| > 3.0, only 0.3% of observations from a normal distribution would naturally fall there.
# That's roughly 1-in-333 chance — rare enough to flag as "worth investigating" without being
# so strict that you catch nothing.

# Why 3.0 specifically (and not 2.0 or 4.0)?
# 2.0 → 5% false positive rate. Too noisy for a trading system — 1-in-20 observations flagged is operationally unmanageable.
# 3.0 → 0.3% false positive rate. The convention in most anomaly detection and Six Sigma quality control. Low enough noise, high enough sensitivity.
# 4.0+ → You'd miss real anomalies. A fill 3.8σ away from normal is genuinely suspicious even if it doesn't clear the 4σ bar.
_ZSCORE_THRESHOLD = 3.0
_MIN_HISTORY_ROWS = 10


@dataclass
class AnomalyWarning:
    check_name: str
    affected_table: str
    affected_rows: int
    warn_value: str
    warn_time: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ZScoreResult:
    is_anomaly: bool
    z_score: float | None = None


class AnomalyDetector:
    """Z-score and volume anomaly detection over rolling 30-day window."""

    def __init__(
        self,
        engine: sa.Engine | None = None,
        quarantine: Quarantine | None = None,
        z_threshold: float = _ZSCORE_THRESHOLD,
        min_history: int = _MIN_HISTORY_ROWS,
    ) -> None:
        self._engine = engine
        self._quarantine = quarantine
        self._z_threshold = z_threshold
        self._min_history = min_history

    def check_zscore(self, values: list[float]) -> ZScoreResult:
        """Check the last value in a series against the Z-score threshold."""
        import numpy as np

        if len(values) < self._min_history:
            return ZScoreResult(is_anomaly=False)
        arr = np.array(values, dtype=float)
        mean = float(arr.mean())
        std = float(arr.std())
        if std == 0:
            return ZScoreResult(is_anomaly=False, z_score=0.0)
        z = abs(arr[-1] - mean) / std
        return ZScoreResult(is_anomaly=float(z) > self._z_threshold, z_score=float(z))

    def check(self, orders: pd.DataFrame, fills: pd.DataFrame) -> list[AnomalyWarning]:
        warnings: list[AnomalyWarning] = []
        warnings.extend(self._check_slippage_zscore(orders))
        warnings.extend(self._check_fill_volume(fills))
        warnings.extend(self._check_order_count(orders))
        return warnings

    def _check_slippage_zscore(self, df: pd.DataFrame) -> list[AnomalyWarning]:
        if df.empty or "arrival_slippage_bps" not in df.columns:
            return []

        warnings: list[AnomalyWarning] = []
        for asset_class, grp in df.groupby("instrument_class"):
            slippage = grp["arrival_slippage_bps"].dropna()
            if len(slippage) < _MIN_HISTORY_ROWS:
                continue
            mean = slippage.mean()
            std = slippage.std()
            if std == 0:
                continue
            mask = (slippage - mean).abs() / std > _ZSCORE_THRESHOLD
            outlier_count = int(mask.sum())
            if outlier_count == 0:
                continue
            warnings.append(
                AnomalyWarning(
                    check_name=f"slippage_zscore_{asset_class}",
                    affected_table="biz_vault.bv_order_enriched",
                    affected_rows=outlier_count,
                    warn_value=f"mean={mean:.2f}bps std={std:.2f}bps outliers={outlier_count}",
                )
            )
            if self._quarantine is not None:
                outlier_rows = grp.loc[mask.index[mask]]
                for _, row in outlier_rows.iterrows():
                    record_id = str(row.get("hub_order_key") or row.name)
                    zscore = abs(row["arrival_slippage_bps"] - mean) / std
                    self._quarantine.quarantine_record(
                        record_id=record_id,
                        source_table="biz_vault.bv_order_enriched",
                        failed_check=f"slippage_zscore_{asset_class}",
                        severity="soft",
                        payload=row.dropna().to_dict(),
                        reason=(
                            f"arrival_slippage_bps={row['arrival_slippage_bps']:.2f}bps "
                            f"is {zscore:.1f}σ from mean ({mean:.2f}bps)"
                        ),
                    )
        return warnings

    def _check_fill_volume(self, df: pd.DataFrame) -> list[AnomalyWarning]:
        if df.empty or "fill_quantity" not in df.columns:
            return []

        warnings: list[AnomalyWarning] = []
        for asset_class, grp in df.groupby("instrument_class"):
            vols = grp["fill_quantity"].dropna()
            if len(vols) < _MIN_HISTORY_ROWS:
                continue
            mean = vols.mean()
            std = vols.std()
            if std == 0:
                continue
            mask = (vols - mean).abs() / std > _ZSCORE_THRESHOLD
            outlier_count = int(mask.sum())
            if outlier_count == 0:
                continue
            warnings.append(
                AnomalyWarning(
                    check_name=f"volume_zscore_{asset_class}",
                    affected_table="raw_vault.sat_fill_execution",
                    affected_rows=outlier_count,
                    warn_value=f"mean_qty={mean:.0f} outliers={outlier_count}",
                )
            )
            if self._quarantine is not None:
                outlier_rows = grp.loc[mask.index[mask]]
                for _, row in outlier_rows.iterrows():
                    record_id = str(
                        row.get("hub_fill_key")
                        or row.get("sat_fill_execution_id")
                        or row.name
                    )
                    zscore = abs(row["fill_quantity"] - mean) / std
                    self._quarantine.quarantine_record(
                        record_id=record_id,
                        source_table="raw_vault.sat_fill_execution",
                        failed_check=f"volume_zscore_{asset_class}",
                        severity="soft",
                        payload=row.dropna().to_dict(),
                        reason=(
                            f"fill_quantity={row['fill_quantity']} "
                            f"is {zscore:.1f}σ from mean ({mean:.0f})"
                        ),
                    )
        return warnings

    def _check_order_count(self, df: pd.DataFrame) -> list[AnomalyWarning]:
        if df.empty:
            return []

        count = len(df)
        if count == 0:
            return [
                AnomalyWarning(
                    check_name="empty_order_set",
                    affected_table="biz_vault.bv_order_enriched",
                    affected_rows=0,
                    warn_value="No orders loaded for trade_date",
                )
            ]
        return []
