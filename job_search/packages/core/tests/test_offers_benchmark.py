"""Unit tests for the offer benchmark."""

from __future__ import annotations

import unittest
from datetime import date

from core.offers.benchmark import (
    Offer,
    PoolRow,
    benchmark,
    mid_rank_percentile,
    normalise,
)
from core.offers.config import load_offers_config

_CFG = load_offers_config()
_MONTH = date(2026, 9, 1)


def _row(figure: float | None, n: int = 0, **kw: object) -> PoolRow:
    """Build a pool row (a contract data-engineer posting by default).

    Args:
        figure: Its stated pay, or None.
        n: A disambiguator for the id.
        **kw: PoolRow fields to replace.

    Returns:
        A `PoolRow`.
    """
    base: dict[str, object] = {
        "job_group_id": f"j{n}-{figure}",
        "title": f"Data Engineer {n}",
        "company": f"Co{n}",
        "category": "data_engineer",
        "region": "UKI",
        "seniority": "senior",
        "contract_type": "contract",
        "ir35_status": "undetermined",
        "figure": figure,
        "month": _MONTH,
    }
    return PoolRow(**{**base, **kw})  # type: ignore[arg-type]


def _offer(amount: float = 500.0, **kw: object) -> Offer:
    """Build a contract offer (day rate, data engineer, UKI, senior).

    Args:
        amount: The amount.
        **kw: Offer fields to replace.

    Returns:
        An `Offer`.
    """
    base: dict[str, object] = {
        "amount": amount,
        "basis": "daily",
        "engagement_type": "contract",
        "ir35_status": "undetermined",
        "category": "data_engineer",
        "region": "UKI",
        "seniority": "senior",
    }
    return Offer(**{**base, **kw})  # type: ignore[arg-type]


def _pool(figures: list[float | None], **kw: object) -> list[PoolRow]:
    """Build pool rows from figures.

    Args:
        figures: Stated pay per row (None = undisclosed).
        **kw: PoolRow fields shared by every row.

    Returns:
        The rows.
    """
    return [_row(f, i, **kw) for i, f in enumerate(figures)]


class TestNormalise(unittest.TestCase):
    """The offer is put into the pool's unit, or refused."""

    def test_units(self) -> None:
        self.assertEqual(normalise(_offer(500), _CFG), (500, "GBP per day"))
        self.assertEqual(
            normalise(_offer(80, basis="hourly"), _CFG), (80 * 7.5, "GBP per day")
        )
        perm = _offer(70000, basis="annual", engagement_type="permanent")
        self.assertEqual(normalise(perm, _CFG), (70000, "GBP per year"))

    def test_a_basis_that_does_not_fit_is_refused(self) -> None:
        for bad in (
            _offer(80000, basis="annual"),
            _offer(500, basis="daily", engagement_type="permanent"),
            _offer(0),
            _offer(-5),
            _offer(500, engagement_type="freelance"),
        ):
            with self.assertRaises(ValueError):
                normalise(bad, _CFG)


class TestPercentile(unittest.TestCase):
    """Mid-rank position."""

    def test_mid_rank(self) -> None:
        figures = [float(i) for i in range(1, 11)]
        self.assertEqual(mid_rank_percentile(5.5, figures), 50.0)
        self.assertEqual(
            mid_rank_percentile(5, figures), 45.0
        )  # 4 below + half of 1 tie
        self.assertEqual(mid_rank_percentile(0, figures), 0.0)
        self.assertEqual(mid_rank_percentile(99, figures), 100.0)


class TestBenchmark(unittest.TestCase):
    """The answer is honest about thinness."""

    def test_enough_data_gives_a_percentile_with_n_and_disclosure_rate(self) -> None:
        pool = _pool(
            [float(x) for x in range(400, 600, 20)] + [None] * 10
        )  # 10 disclosed of 20
        result = benchmark(_offer(500), pool, _CFG)
        self.assertEqual(result.status, "ok")
        self.assertEqual((result.n_disclosed, result.n_postings), (10, 20))
        self.assertEqual(result.disclosure_rate, 0.5)
        self.assertEqual(result.percentile, 55.0)  # 5 below + half of the tie at 500
        self.assertEqual((result.p25, result.median, result.p75), (445.0, 490.0, 535.0))

    def test_too_thin_gives_no_percentile_but_still_states_n_and_a_warning(
        self,
    ) -> None:
        pool = _pool([float(x) for x in range(400, 580, 20)])  # 9 disclosed
        result = benchmark(_offer(500), pool, _CFG)
        self.assertEqual(result.status, "too_thin")
        self.assertIsNone(result.percentile)
        self.assertIsNone(result.median)
        self.assertEqual(result.n_disclosed, 9)
        self.assertTrue(any("Too thin" in w for w in result.warnings))
        self.assertTrue(result.comparables)  # still shown, with their n

    def test_a_wider_slice_is_used_when_the_narrow_one_is_thin_and_the_warning_says_so(
        self,
    ) -> None:
        narrow = _pool([450.0, 500.0, 550.0], seniority="senior")
        wide = [_row(float(400 + 10 * i), 100 + i, seniority="mid") for i in range(12)]
        result = benchmark(_offer(500), narrow + wide, _CFG)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.n_disclosed, 15)  # region-wide: both seniorities
        self.assertNotIn("seniority", result.slice_label or "")
        self.assertTrue(
            any("Narrower slices were too thin" in w for w in result.warnings)
        )
        self.assertEqual(result.ladder[0].n_disclosed, 3)

    def test_a_thin_answer_reports_your_category_not_the_market_wide_rung(self) -> None:
        mine = _pool([450.0, 500.0, 520.0], category="data_engineer")
        others = [
            _row(float(300 + 10 * i), 50 + i, category="software_engineer")
            for i in range(6)
        ]
        result = benchmark(_offer(500), mine + others, _CFG)
        self.assertEqual(result.status, "too_thin")
        self.assertEqual((result.n_disclosed, result.n_postings), (3, 3))
        self.assertNotIn("market-wide", result.slice_label)
        self.assertIn("data_engineer", result.slice_label)
        self.assertTrue(
            all(c.title and "Data Engineer" in c.title for c in result.comparables)
        )

    def test_a_low_disclosure_rate_is_called_out(self) -> None:
        pool = _pool([float(x) for x in range(400, 600, 20)] + [None] * 40)
        result = benchmark(_offer(500), pool, _CFG)
        self.assertTrue(any("state pay" in w for w in result.warnings))

    def test_permanent_postings_never_enter_a_contract_slice(self) -> None:
        contract = _pool([float(x) for x in range(400, 600, 20)])
        permanent = [
            _row(60000.0 + i, 500 + i, contract_type="permanent") for i in range(30)
        ]
        result = benchmark(_offer(500), contract + permanent, _CFG)
        self.assertEqual(result.n_postings, 10)
        self.assertEqual(result.unit, "GBP per day")

    def test_other_categories_are_only_used_in_the_flagged_market_wide_rung(
        self,
    ) -> None:
        mine = _pool([450.0, 500.0], category="data_engineer")
        others = [
            _row(float(300 + 10 * i), 50 + i, category="software_engineer")
            for i in range(20)
        ]
        result = benchmark(_offer(500), mine + others, _CFG)
        self.assertEqual(result.status, "ok")
        self.assertIn("market-wide, not your category", result.slice_label or "")

    def test_unknown_region_and_undetermined_ir35_do_not_filter(self) -> None:
        pool = [
            _row(float(400 + 10 * i), i, region=r)
            for i, r in enumerate(["UKI", "UKK"] * 6)
        ]
        result = benchmark(
            _offer(500, region="unknown", ir35_status="undetermined"), pool, _CFG
        )
        self.assertEqual(result.n_postings, 12)
        self.assertNotIn("ir35", result.ladder[0].label)

    def test_a_stated_ir35_status_narrows_the_first_rung_only(self) -> None:
        pool = _pool([float(400 + 10 * i) for i in range(12)], ir35_status="outside")
        pool += _pool([300.0] * 12, ir35_status="inside")
        result = benchmark(_offer(500, ir35_status="outside"), pool, _CFG)
        self.assertIn("ir35 status outside", result.ladder[0].label)
        self.assertEqual(result.ladder[0].n_postings, 12)

    def test_comparables_are_the_nearest_disclosed_postings_and_capped(self) -> None:
        pool = _pool([float(x) for x in range(400, 700, 10)])
        result = benchmark(_offer(500), pool, _CFG)
        self.assertEqual(len(result.comparables), 8)
        distances = [abs(c.figure - 500) for c in result.comparables]
        self.assertEqual(distances, sorted(distances))
        self.assertTrue(all(c.figure is not None for c in result.comparables))

    def test_direction_is_insufficient_history_without_two_full_windows(self) -> None:
        result = benchmark(
            _offer(500), _pool([float(x) for x in range(400, 600, 10)]), _CFG
        )
        self.assertEqual(result.direction, "insufficient history")
        self.assertIn("needs", result.trend_note)

    def test_direction_rising_when_both_windows_are_full(self) -> None:
        old = [_row(400.0 + i, i, month=date(2026, 3, 1)) for i in range(10)]
        new = [_row(500.0 + i, 100 + i, month=date(2026, 9, 1)) for i in range(10)]
        result = benchmark(_offer(500), old + new, _CFG)
        self.assertEqual(result.direction, "rising")
        self.assertIn("%", result.trend_note)

    def test_an_empty_pool_is_too_thin_not_a_crash(self) -> None:
        result = benchmark(_offer(500), [], _CFG)
        self.assertEqual(
            (result.status, result.n_disclosed, result.n_postings), ("too_thin", 0, 0)
        )
        self.assertEqual(result.comparables, [])


if __name__ == "__main__":
    unittest.main()
