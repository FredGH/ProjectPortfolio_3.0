"""Generate synthetic MMM weekly spend data for the Cortex Signal-to-Action project.

Reads Olist orders + order_items CSVs to aggregate weekly revenue when available.
Falls back to calibrated synthetic revenue when the raw CSVs are not present (dev/CI).
Writes a reproducible olist_mmm_weekly_spend.csv to the seeds/ directory.

Usage:
    python seeds/generate_mmm_spend.py

Raw Olist input files (optional — enables real revenue):
    data/olist_orders_dataset.csv
    data/olist_order_items_dataset.csv
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

RANDOM_SEED = 42
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
OUTPUT_PATH = SCRIPT_DIR / "olist_mmm_weekly_spend.csv"

# ISO weeks 2016-W01 → 2018-W35 ≈ 130 weeks
DATE_START = "2016-01-04"   # Monday of 2016-W01
DATE_END = "2018-08-26"     # Monday of 2018-W35; end of Olist coverage

# São Paulo monthly averages (°C) — source: INMET climatological normals
SP_AVG_TEMP: dict[int, float] = {
    1: 23.5, 2: 24.0, 3: 23.0, 4: 21.0, 5: 18.5, 6: 17.0,
    7: 16.5, 8: 17.5, 9: 19.0, 10: 21.0, 11: 22.0, 12: 23.0,
}

# Brazilian fixed national holidays (month, day)
_FIXED_HOLIDAYS: dict[tuple[int, int], str] = {
    (1, 1): "new_year",
    (4, 21): "tiradentes",
    (5, 1): "labor_day",
    (9, 7): "independence",
    (10, 12): "nossa_senhora",
    (11, 2): "all_souls",
    (11, 15): "republic_day",
    (12, 25): "christmas",
}

# Adstock geometric decay rates per channel — calibrated to typical media half-lives
ADSTOCK_DECAY: dict[str, float] = {
    "tv_spend": 0.70,          # ~3 week half-life
    "paid_search_spend": 0.15, # near-immediate (same-week) response
    "social_spend": 0.40,      # ~1.5 week half-life
    "email_spend": 0.05,       # immediate; no meaningful carryover
    "display_spend": 0.35,     # ~1 week half-life
}


def _easter(year: int) -> pd.Timestamp:
    """Return Easter Sunday for the given year (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lv = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lv) // 451
    month, day = divmod(h + lv - 7 * m + 114, 31)
    return pd.Timestamp(year=year, month=month, day=day + 1)


def _black_friday(year: int) -> pd.Timestamp:
    """Return Black Friday (day after the 4th Thursday in November)."""
    nov1 = pd.Timestamp(year=year, month=11, day=1)
    # dayofweek: Mon=0 … Thu=3
    first_thu = nov1 + pd.Timedelta(days=(3 - nov1.dayofweek) % 7)
    return first_thu + pd.Timedelta(weeks=3, days=1)


def _holiday_map(years: range) -> dict[pd.Timestamp, str]:
    """Return {date: holiday_name} for all years in range."""
    hmap: dict[pd.Timestamp, str] = {}
    for year in years:
        for (m, d), name in _FIXED_HOLIDAYS.items():
            hmap[pd.Timestamp(year=year, month=m, day=d)] = name
        easter = _easter(year)
        hmap[easter] = "easter"
        hmap[easter - pd.Timedelta(days=2)] = "good_friday"
        # Carnival: Monday + Tuesday 48/47 days before Easter
        hmap[easter - pd.Timedelta(days=48)] = "carnival"
        hmap[easter - pd.Timedelta(days=47)] = "carnival"
        hmap[_black_friday(year)] = "black_friday"
    return hmap


def _week_has_date(week_start: pd.Timestamp, dates: set[pd.Timestamp]) -> bool:
    """Return True if any date in `dates` falls in the ISO week starting on week_start."""
    return any((week_start + pd.Timedelta(days=i)) in dates for i in range(7))


def _load_olist_revenue(weeks: pd.DatetimeIndex) -> np.ndarray | None:
    """Load real Olist revenue aggregated to ISO week, aligned to `weeks`. Returns None if CSVs absent."""
    orders_path = DATA_DIR / "olist_orders_dataset.csv"
    items_path = DATA_DIR / "olist_order_items_dataset.csv"
    if not (orders_path.exists() and items_path.exists()):
        return None

    orders = pd.read_csv(
        orders_path,
        usecols=["order_id", "order_purchase_timestamp"],
        parse_dates=["order_purchase_timestamp"],
    )
    items = pd.read_csv(items_path, usecols=["order_id", "price", "freight_value"])
    merged = orders.merge(items, on="order_id")
    merged["week_start"] = (
        merged["order_purchase_timestamp"]
        .dt.to_period("W-SUN")
        .apply(lambda p: p.start_time.normalize())
    )
    merged["revenue"] = merged["price"] + merged["freight_value"]
    olist_weekly = (
        merged.groupby("week_start")["revenue"]
        .sum()
        .rename("weekly_revenue")
        .reset_index()
    )
    aligned = (
        pd.DataFrame({"week_start": weeks})
        .merge(olist_weekly, on="week_start", how="left")
    )
    return aligned["weekly_revenue"].fillna(0.0).to_numpy()


def _synthetic_revenue(weeks: pd.DatetimeIndex, rng: np.random.Generator) -> np.ndarray:
    """Calibrated synthetic revenue: exponential growth + seasonality + Gaussian noise.

    Approximate Olist revenue profile: ~60k BRL/week (Jan 2016) → ~300k BRL/week (Aug 2018).
    """
    n = len(weeks)
    t = np.arange(n, dtype=float)
    trend = 60_000.0 * np.exp(0.012 * t)
    month = pd.DatetimeIndex(weeks).month
    seasonal = np.ones(n)
    seasonal = np.where(np.isin(month, [12]), seasonal * 1.35, seasonal)
    seasonal = np.where(np.isin(month, [11]), seasonal * 1.20, seasonal)
    seasonal = np.where(np.isin(month, [1]), seasonal * 0.80, seasonal)
    seasonal = np.where(np.isin(month, [2]), seasonal * 0.85, seasonal)
    noise = rng.normal(loc=0.0, scale=0.07, size=n)
    return trend * seasonal * (1.0 + noise)


def _adstock(spend: np.ndarray, decay: float) -> np.ndarray:
    """Apply geometric adstock (infinite lag model) with given decay rate."""
    out = np.empty_like(spend)
    out[0] = spend[0]
    for i in range(1, len(spend)):
        out[i] = spend[i] + decay * out[i - 1]
    return out


def _generate_channel_spend(
    weeks: pd.DatetimeIndex, rng: np.random.Generator
) -> dict[str, np.ndarray]:
    """Return adstock-transformed spend arrays, calibrated to typical Brazilian SME budgets."""
    n = len(weeks)
    month = pd.DatetimeIndex(weeks).month
    q4 = np.isin(month, [10, 11, 12]).astype(float)

    raw: dict[str, np.ndarray] = {
        "tv_spend": rng.uniform(15_000, 35_000, n) * (1.0 + 0.50 * q4),
        "paid_search_spend": rng.uniform(8_000, 20_000, n) * (1.0 + 0.30 * q4),
        "social_spend": rng.uniform(4_000, 12_000, n) * (1.0 + 0.40 * q4),
        "email_spend": rng.uniform(1_500, 4_500, n),
        "display_spend": rng.uniform(3_000, 9_000, n) * (1.0 + 0.25 * q4),
    }
    return {col: _adstock(raw[col], ADSTOCK_DECAY[col]) for col in raw}


def _competitor_index(n: int, rng: np.random.Generator) -> np.ndarray:
    """Ornstein-Uhlenbeck-style mean-reverting walk clamped to [0.5, 1.5]."""
    ci = np.empty(n)
    ci[0] = 1.0
    for i in range(1, n):
        ci[i] = ci[i - 1] + rng.normal(0.0, 0.04) + 0.005 * (1.0 - ci[i - 1])
    return np.clip(ci, 0.5, 1.5)


def generate() -> None:
    """Generate olist_mmm_weekly_spend.csv and write it to seeds/."""
    rng = np.random.default_rng(RANDOM_SEED)
    weeks = pd.date_range(start=DATE_START, end=DATE_END, freq="W-MON")
    n = len(weeks)

    # Revenue
    rev = _load_olist_revenue(weeks)
    if rev is not None:
        print(f"Loaded Olist revenue for {n} ISO weeks.")
    else:
        warnings.warn(
            "Olist CSVs not found in data/ — using synthetic revenue. "
            "Place olist_orders_dataset.csv and olist_order_items_dataset.csv in data/ "
            "for real revenue figures.",
            UserWarning,
            stacklevel=2,
        )
        rev = _synthetic_revenue(weeks, rng)

    spend = _generate_channel_spend(weeks, rng)

    years = range(weeks[0].year, weeks[-1].year + 1)
    hmap = _holiday_map(years)
    all_holidays = set(hmap.keys())
    bf_dates = {d for d, name in hmap.items() if name == "black_friday"}

    holiday_flag = np.array([int(_week_has_date(w, all_holidays)) for w in weeks], dtype=int)
    black_friday_flag = np.array([int(_week_has_date(w, bf_dates)) for w in weeks], dtype=int)
    comp_idx = _competitor_index(n, rng)
    avg_temp = np.array([SP_AVG_TEMP[w.month] for w in weeks], dtype=float)

    df = pd.DataFrame(
        {
            "iso_week": [w.strftime("%G-W%V") for w in weeks],
            "week_start_date": weeks.strftime("%Y-%m-%d"),
            "weekly_revenue": np.round(rev, 2),
            "tv_spend": np.round(spend["tv_spend"], 2),
            "paid_search_spend": np.round(spend["paid_search_spend"], 2),
            "social_spend": np.round(spend["social_spend"], 2),
            "email_spend": np.round(spend["email_spend"], 2),
            "display_spend": np.round(spend["display_spend"], 2),
            "holiday_flag": holiday_flag,
            "black_friday_flag": black_friday_flag,
            "competitor_index": np.round(comp_idx, 4),
            "avg_temperature": avg_temp,
        }
    )

    df.to_csv(OUTPUT_PATH, index=False)
    print(f"Written {len(df)} rows → {OUTPUT_PATH}")


if __name__ == "__main__":
    generate()
