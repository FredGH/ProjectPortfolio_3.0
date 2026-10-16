"""Checking an offer: benchmark plus take-home (PLAN.md Step 21c)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from core.offers.benchmark import Offer, PoolRow, benchmark, normalise
from core.offers.config import OffersConfig
from core.offers.tax import NetResult, TaxConfig, net_for


def _net(result: NetResult) -> dict[str, Any]:
    """Make a take-home result JSON-friendly.

    Args:
        result: The estimate.

    Returns:
        The estimate as a dict with the steps as label/amount pairs.
    """
    out = asdict(result)
    out["steps"] = [
        {"label": label, "amount": amount} for label, amount in result.steps
    ]
    return out


def check_offer(
    offer: Offer, pool: list[PoolRow], config: OffersConfig, tax: TaxConfig
) -> dict[str, Any]:
    """Benchmark an offer and, when it can be, estimate its take-home.

    Args:
        offer: The offer.
        pool: The benchmark pool rows.
        config: The offers config.
        tax: The tax config.

    Returns:
        `{"benchmark": ..., "net": ...}`. `net` is present for a contract
        offer whose IR35 status is `inside` or `outside`, computed on the
        day rate; otherwise None (an undetermined status is not guessed).

    Raises:
        ValueError: If the offer cannot be normalised.
    """
    value, _unit = normalise(offer, config)
    result = benchmark(offer, pool, config)
    can_net = offer.engagement_type == "contract" and offer.ir35_status in (
        "inside",
        "outside",
    )
    return {
        "benchmark": asdict(result),
        "net": _net(net_for(value, offer.ir35_status, tax)) if can_net else None,
        "net_note": (
            None
            if can_net
            else (
                "Take-home needs a contract offer with a known IR35 status (inside or "
                "outside)."
            )
        ),
    }


def compare_net(
    first: tuple[float, str], second: tuple[float, str], tax: TaxConfig
) -> dict[str, Any]:
    """Compare the take-home of two day rates under their IR35 statuses.

    Args:
        first: `(day_rate, ir35_status)` of the first offer.
        second: `(day_rate, ir35_status)` of the second offer.
        tax: The tax config.

    Returns:
        Both estimates and the difference in take-home per year and per day
        (second minus first).

    Raises:
        ValueError: If a status is not `inside` or `outside`.
    """
    a, b = net_for(first[0], first[1], tax), net_for(second[0], second[1], tax)
    return {
        "first": _net(a),
        "second": _net(b),
        "difference_year": b.net_year - a.net_year,
        "difference_day": b.net_day - a.net_day,
    }
