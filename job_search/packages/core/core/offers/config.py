"""Offer-check settings from `config/offers.yml`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parents[4] / "config" / "offers.yml"


@dataclass(frozen=True)
class OffersConfig:
    """Thresholds for the benchmark.

    Attributes:
        min_disclosed: Disclosed figures a slice needs for a percentile.
        low_disclosure_rate: Below this share of postings stating pay, warn.
        hours_per_day: Hours converting an hourly rate to a day rate.
        comparables: How many comparable postings to show.
        trend_window_months: Length of the recent and earlier windows.
        trend_flat_band: Fractional median move treated as flat.
    """

    min_disclosed: int
    low_disclosure_rate: float
    hours_per_day: float
    comparables: int
    trend_window_months: int
    trend_flat_band: float


def load_offers_config(path: Path | None = None) -> OffersConfig:
    """Read the offers config.

    Args:
        path: Override for tests; defaults to `config/offers.yml`.

    Returns:
        The config.

    Raises:
        ValueError: If a value is missing or not positive.
    """
    raw = yaml.safe_load((path or _DEFAULT_PATH).read_text()) or {}
    values = {}
    for key in (
        "min_disclosed",
        "low_disclosure_rate",
        "hours_per_day",
        "comparables",
        "trend_window_months",
        "trend_flat_band",
    ):
        value = raw.get(key)
        if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{key} must be a positive number, got {value!r}")
        values[key] = value
    return OffersConfig(
        min_disclosed=int(values["min_disclosed"]),
        low_disclosure_rate=float(values["low_disclosure_rate"]),
        hours_per_day=float(values["hours_per_day"]),
        comparables=int(values["comparables"]),
        trend_window_months=int(values["trend_window_months"]),
        trend_flat_band=float(values["trend_flat_band"]),
    )
