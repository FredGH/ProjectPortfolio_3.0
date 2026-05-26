from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterator

import dlt
import numpy as np

# BOND-001 … BOND-010  (must match ref_data_source.py)
_BONDS: list[dict] = [
    {
        "instrument_id": "BOND-001",
        "coupon": 2.50,
        "years_to_maturity": 5.0,
        "country": "DE",
    },
    {
        "instrument_id": "BOND-002",
        "coupon": 1.75,
        "years_to_maturity": 4.0,
        "country": "FR",
    },
    {
        "instrument_id": "BOND-003",
        "coupon": 3.00,
        "years_to_maturity": 6.0,
        "country": "IT",
    },
    {
        "instrument_id": "BOND-004",
        "coupon": 2.35,
        "years_to_maturity": 4.0,
        "country": "ES",
    },
    {
        "instrument_id": "BOND-005",
        "coupon": 1.625,
        "years_to_maturity": 3.0,
        "country": "GB",
    },
    {
        "instrument_id": "BOND-006",
        "coupon": 2.00,
        "years_to_maturity": 3.0,
        "country": "NL",
    },
    {
        "instrument_id": "BOND-007",
        "coupon": 1.90,
        "years_to_maturity": 2.0,
        "country": "BE",
    },
    {
        "instrument_id": "BOND-008",
        "coupon": 2.10,
        "years_to_maturity": 5.0,
        "country": "AT",
    },
    {
        "instrument_id": "BOND-009",
        "coupon": 1.875,
        "years_to_maturity": 4.0,
        "country": "FI",
    },
    {
        "instrument_id": "BOND-010",
        "coupon": 3.15,
        "years_to_maturity": 5.0,
        "country": "PT",
    },
]

# Risk-free rate by jurisdiction (EUR OIS approximation)
_BASE_YIELD = 0.038  # ECB policy rate proxy
_SPREADS = {
    "DE": 0.00,
    "FR": 0.01,
    "NL": 0.01,
    "BE": 0.015,
    "AT": 0.012,
    "FI": 0.008,
    "IT": 0.025,
    "ES": 0.022,
    "PT": 0.035,
    "GB": 0.005,
}


def _bond_price(
    coupon_pct: float, yield_: float, n_years: float, freq: int = 2
) -> float:
    """Clean price (% of par) using standard bond pricing formula."""
    c = coupon_pct / 100 / freq
    n = int(n_years * freq)
    y = yield_ / freq
    if y == 0:
        return 100.0 + c * n * 100
    pv_coupons = c * 100 * (1 - (1 + y) ** -n) / y
    pv_par = 100 / (1 + y) ** n
    return round(pv_coupons + pv_par, 6)


def _dv01(
    coupon_pct: float, yield_: float, n_years: float, clean_price: float
) -> float:
    """DV01 in EUR per 100 nominal (modified duration method)."""
    freq = 2
    n = int(n_years * freq)
    y = yield_ / freq
    c = coupon_pct / 100 / freq

    if y == 0:
        return 0.0

    # Modified duration
    mac_dur_num = sum(t * c * 100 / (1 + y) ** t for t in range(1, n + 1))
    mac_dur_num += n * 100 / (1 + y) ** n
    mac_dur = mac_dur_num / clean_price
    mod_dur = mac_dur / (1 + y)

    return round(mod_dur * clean_price / 10_000, 6)  # per bp


@dlt.source(name="fi_pricing")
def fi_pricing_source(trade_date: date | None = None) -> Iterator:
    if trade_date is None:
        trade_date = date(2025, 1, 15)

    rng = np.random.default_rng(seed=99)
    loaded_at = datetime.now(tz=timezone.utc)

    @dlt.resource(
        name="bond_prices",
        write_disposition="merge",
        primary_key=["instrument_id", "price_date"],
    )
    def bond_prices() -> Iterator[dict]:
        for bond in _BONDS:
            spread = _SPREADS.get(bond["country"], 0.015)
            # Add small daily noise to spread
            spread_noise = float(rng.normal(0, 0.001))
            yield_ = round(_BASE_YIELD + spread + spread_noise, 6)
            clean = _bond_price(bond["coupon"], yield_, bond["years_to_maturity"])
            accrued = round(bond["coupon"] / 100 / 2 * 0.5, 6)  # mid-period approx
            dirty = round(clean + accrued, 6)
            dv01 = _dv01(bond["coupon"], yield_, bond["years_to_maturity"], clean)

            yield {
                "instrument_id": bond["instrument_id"],
                "price_date": trade_date.isoformat(),
                "yield_pct": round(yield_ * 100, 6),
                "clean_price": clean,
                "accrued_interest": accrued,
                "dirty_price": dirty,
                "dv01_per_100": dv01,
                "duration_years": round(bond["years_to_maturity"] * 0.9, 4),
                "coupon_pct": bond["coupon"],
                "country": bond["country"],
                "_loaded_at": loaded_at,
            }

    return (bond_prices,)
