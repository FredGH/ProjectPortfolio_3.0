"""Where an offer sits in the market, honestly (PLAN.md Step 21c).

Pure functions over the benchmark pool. An answer never carries a bare
percentile: it always states how many postings disclosed pay (`n_disclosed`),
how many there were (`n_postings`) and the disclosure rate; and when the slice
is too thin it computes no percentile at all.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

from core.offers.config import OffersConfig


@dataclass(frozen=True)
class PoolRow:
    """One posting in the benchmark pool.

    Attributes:
        job_group_id: The job.
        title: Its display title.
        company: The employer.
        category: The category.
        region: The region.
        seniority: The seniority band.
        contract_type: `contract` or `permanent`.
        ir35_status: `inside`, `outside` or `undetermined`.
        figure: The stated pay (GBP per day or per year), or None.
        month: The month we first saw it.
    """

    job_group_id: str
    title: str | None
    company: str | None
    category: str
    region: str
    seniority: str
    contract_type: str
    ir35_status: str
    figure: float | None
    month: date


@dataclass(frozen=True)
class Offer:
    """An offer to benchmark.

    Attributes:
        amount: The amount offered.
        basis: `daily`, `hourly` or `annual`.
        engagement_type: `contract` or `permanent`.
        ir35_status: `inside`, `outside` or `undetermined` (contract only).
        category: The offer's category.
        region: The region (`unknown` or blank skips the region filter).
        seniority: The seniority band (blank skips the filter).
    """

    amount: float
    basis: str
    engagement_type: str
    ir35_status: str
    category: str
    region: str
    seniority: str


@dataclass(frozen=True)
class SliceInfo:
    """One rung of the slice ladder.

    Attributes:
        label: Which filters this rung applies.
        n_postings: Postings in the slice.
        n_disclosed: Postings with a stated figure.
        disclosure_rate: `n_disclosed / n_postings` (0 when empty).
    """

    label: str
    n_postings: int
    n_disclosed: int
    disclosure_rate: float


@dataclass(frozen=True)
class Comparable:
    """A comparable posting with a disclosed figure.

    Attributes:
        job_group_id: The job.
        title: Its title.
        company: The employer.
        figure: Its stated pay.
        region: Its region.
        seniority: Its seniority.
    """

    job_group_id: str
    title: str | None
    company: str | None
    figure: float
    region: str
    seniority: str


@dataclass
class Benchmark:
    """The answer.

    Attributes:
        value: The offer in the pool's unit.
        unit: `GBP per day` or `GBP per year`.
        status: `ok` (a percentile is given) or `too_thin`.
        percentile: The offer's mid-rank percentile in the slice, or None.
        slice_label: The slice the percentile is from (or None).
        n_disclosed: Disclosed figures in the reported slice.
        n_postings: Postings in the reported slice.
        disclosure_rate: Their share.
        p25: 25th percentile of the slice's figures (None when too thin).
        median: Median (None when too thin).
        p75: 75th percentile (None when too thin).
        ladder: Every rung tried, narrowest first.
        comparables: Disclosed postings nearest the offer.
        direction: `rising`, `falling`, `flat` or `insufficient history`.
        trend_note: What the direction compares.
        warnings: Plain-language cautions, always including any thinness.
    """

    value: float
    unit: str
    status: str
    percentile: float | None
    slice_label: str | None
    n_disclosed: int
    n_postings: int
    disclosure_rate: float
    p25: float | None
    median: float | None
    p75: float | None
    ladder: list[SliceInfo]
    comparables: list[Comparable]
    direction: str
    trend_note: str
    warnings: list[str] = field(default_factory=list)


def normalise(offer: Offer, config: OffersConfig) -> tuple[float, str]:
    """Express an offer in the pool's unit.

    Args:
        offer: The offer.
        config: The offers config (hours per day).

    Returns:
        `(value, unit)`: a contract offer as GBP per day, a permanent offer as
        GBP per year.

    Raises:
        ValueError: If the amount is not positive, the engagement is unknown,
            or the basis does not fit the engagement (an annual figure for a
            contract, a day rate for a permanent role).
    """
    if offer.amount <= 0:
        raise ValueError("the amount must be positive")
    if offer.engagement_type == "contract":
        if offer.basis == "daily":
            return offer.amount, "GBP per day"
        if offer.basis == "hourly":
            return offer.amount * config.hours_per_day, "GBP per day"
        raise ValueError("a contract offer must be a daily or hourly rate")
    if offer.engagement_type == "permanent":
        if offer.basis == "annual":
            return offer.amount, "GBP per year"
        raise ValueError("a permanent offer must be an annual salary")
    raise ValueError("engagement type must be 'contract' or 'permanent'")


def _rungs(offer: Offer) -> list[tuple[str, dict[str, str]]]:
    """List the slice ladder for an offer, narrowest first.

    Args:
        offer: The offer.

    Returns:
        `(label, filters)` rungs. Filters name PoolRow fields; blank or
        `unknown` region/seniority are skipped. The last rung is the whole
        contract type (market-wide).
    """
    base = {"contract_type": offer.engagement_type, "category": offer.category}
    region = {"region": offer.region} if offer.region not in ("", "unknown") else {}
    seniority = (
        {"seniority": offer.seniority} if offer.seniority not in ("", "unknown") else {}
    )
    ir35 = (
        {"ir35_status": offer.ir35_status}
        if offer.engagement_type == "contract"
        and offer.ir35_status in ("inside", "outside")
        else {}
    )
    candidates = [
        {**base, **region, **seniority, **ir35},
        {**base, **region, **seniority},
        {**base, **region},
        base,
        {"contract_type": offer.engagement_type},
    ]
    rungs: list[tuple[str, dict[str, str]]] = []
    seen: list[dict[str, str]] = []
    for filters in candidates:
        if filters in seen:
            continue
        seen.append(filters)
        label = " + ".join(f"{k.replace('_', ' ')} {v}" for k, v in filters.items())
        if list(filters) == ["contract_type"]:
            label += " (market-wide, not your category)"
        rungs.append((label, filters))
    return rungs


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated quantile.

    Args:
        sorted_values: Ascending, non-empty values.
        q: The quantile, 0-1.

    Returns:
        The interpolated value.
    """
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (
        position - low
    )


def mid_rank_percentile(value: float, figures: list[float]) -> float:
    """The offer's mid-rank percentile among figures.

    Args:
        value: The offered figure.
        figures: Disclosed figures (non-empty).

    Returns:
        100 x (figures below + half the ties) / n.
    """
    below = sum(1 for f in figures if f < value)
    equal = sum(1 for f in figures if f == value)
    return 100.0 * (below + 0.5 * equal) / len(figures)


def _add_months(day: date, months: int) -> date:
    """Shift a first-of-month date by whole months.

    Args:
        day: A date (its day is ignored).
        months: Months to add (may be negative).

    Returns:
        The first of the resulting month.
    """
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def direction(
    rows: list[PoolRow], latest: date, config: OffersConfig
) -> tuple[str, str]:
    """Is the slice rising or falling over six months?

    Args:
        rows: The slice's rows.
        latest: The latest month in the whole pool.
        config: The offers config (window length, flat band, minimum n).

    Returns:
        `(direction, note)`. The median of the last `trend_window_months` is
        compared with the same window six months earlier, but only if both
        windows hold `min_disclosed` figures; otherwise `insufficient history`.
    """
    w = config.trend_window_months
    recent_start, then_end = _add_months(latest, -(w - 1)), _add_months(latest, -6)
    then_start = _add_months(then_end, -(w - 1))
    recent = [
        r.figure
        for r in rows
        if r.figure is not None and recent_start <= r.month <= latest
    ]
    earlier = [
        r.figure
        for r in rows
        if r.figure is not None and then_start <= r.month <= then_end
    ]
    if len(recent) < config.min_disclosed or len(earlier) < config.min_disclosed:
        return (
            "insufficient history",
            f"needs {config.min_disclosed} disclosed figures in both the last {w} "
            f"months ({len(recent)}) and the {w} months ending 6 months earlier "
            f"({len(earlier)})",
        )
    now_m, then_m = statistics.median(recent), statistics.median(earlier)
    change = (now_m - then_m) / then_m
    label = (
        "flat"
        if abs(change) < config.trend_flat_band
        else ("rising" if change > 0 else "falling")
    )
    return label, f"median {then_m:,.0f} -> {now_m:,.0f} ({change:+.1%})"


def benchmark(offer: Offer, pool: list[PoolRow], config: OffersConfig) -> Benchmark:
    """Place an offer within the market.

    Args:
        offer: The offer.
        pool: The benchmark pool rows (all categories and types).
        config: The offers config.

    Returns:
        The answer. `status` is `too_thin` and `percentile` is None unless a
        rung of the ladder holds at least `min_disclosed` disclosed figures;
        the narrowest such rung is used. `n_disclosed`, `n_postings` and the
        disclosure rate are always filled from the reported slice (the chosen
        rung, or the widest rung when none qualifies).

    Raises:
        ValueError: If the offer cannot be normalised (see `normalise`).
    """
    value, unit = normalise(offer, config)
    latest = max((r.month for r in pool), default=date.today())
    ladder: list[SliceInfo] = []
    slices: list[list[PoolRow]] = []
    for label, filters in _rungs(offer):
        rows = [r for r in pool if all(getattr(r, k) == v for k, v in filters.items())]
        disclosed = [r for r in rows if r.figure is not None]
        ladder.append(
            SliceInfo(
                label,
                len(rows),
                len(disclosed),
                len(disclosed) / len(rows) if rows else 0.0,
            )
        )
        slices.append(rows)
    chosen = next(
        (i for i, s in enumerate(ladder) if s.n_disclosed >= config.min_disclosed), None
    )
    report = chosen if chosen is not None else len(ladder) - 1
    rows = slices[report]
    figures = sorted(r.figure for r in rows if r.figure is not None)
    info = ladder[report]
    warnings: list[str] = []
    if chosen is None:
        warnings.append(
            f"Too thin: no slice has {config.min_disclosed} disclosed figures "
            f"(the widest has {info.n_disclosed}), so no percentile is given. A "
            "number from this few postings would not be a finding."
        )
    elif chosen > 0:
        warnings.append(
            f"Narrower slices were too thin ({ladder[0].n_disclosed} disclosed in "
            f"'{ladder[0].label}'); this is the narrowest with enough data."
        )
    if info.n_postings and info.disclosure_rate < config.low_disclosure_rate:
        warnings.append(
            f"Only {info.disclosure_rate:.0%} of postings in this slice state pay, "
            "so the disclosed figures may not represent the market."
        )
    near = sorted(
        (r for r in rows if r.figure is not None), key=lambda r: abs(r.figure - value)
    )
    comparables = [
        Comparable(r.job_group_id, r.title, r.company, r.figure, r.region, r.seniority)
        for r in near[: config.comparables]
    ]
    ok = chosen is not None
    trend, note = (
        direction(rows, latest, config)
        if ok
        else ("insufficient history", "slice too thin")
    )
    return Benchmark(
        value=value,
        unit=unit,
        status="ok" if ok else "too_thin",
        percentile=mid_rank_percentile(value, figures) if ok else None,
        slice_label=info.label if ok else None,
        n_disclosed=info.n_disclosed,
        n_postings=info.n_postings,
        disclosure_rate=info.disclosure_rate,
        p25=_percentile(figures, 0.25) if ok else None,
        median=_percentile(figures, 0.5) if ok else None,
        p75=_percentile(figures, 0.75) if ok else None,
        ladder=ladder,
        comparables=comparables,
        direction=trend,
        trend_note=note,
        warnings=warnings,
    )
