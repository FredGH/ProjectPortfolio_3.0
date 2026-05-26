"""Unit tests for cost_decomposition analytics module."""

from __future__ import annotations

import unittest

from analytics.modules.cost_decomposition import almgren_chriss_impact


class TestAlmgrenChrissImpact(unittest.TestCase):
    def test_returns_positive_bps_for_buy(self) -> None:
        result = almgren_chriss_impact(
            quantity=10_000, adv=1_000_000, vol_daily=0.015, side="BUY"
        )
        self.assertGreater(result, 0)

    def test_returns_positive_bps_for_sell(self) -> None:
        result = almgren_chriss_impact(
            quantity=10_000, adv=1_000_000, vol_daily=0.015, side="SELL"
        )
        self.assertGreater(result, 0)

    def test_higher_participation_gives_higher_impact(self) -> None:
        low = almgren_chriss_impact(
            quantity=1_000, adv=1_000_000, vol_daily=0.015, side="BUY"
        )
        high = almgren_chriss_impact(
            quantity=100_000, adv=1_000_000, vol_daily=0.015, side="BUY"
        )
        self.assertGreater(high, low)

    def test_higher_vol_gives_higher_impact(self) -> None:
        low_vol = almgren_chriss_impact(
            quantity=10_000, adv=1_000_000, vol_daily=0.005, side="BUY"
        )
        high_vol = almgren_chriss_impact(
            quantity=10_000, adv=1_000_000, vol_daily=0.025, side="BUY"
        )
        self.assertGreater(high_vol, low_vol)

    def test_zero_adv_does_not_raise(self) -> None:
        result = almgren_chriss_impact(
            quantity=10_000, adv=0, vol_daily=0.015, side="BUY"
        )
        self.assertGreaterEqual(result, 0)

    def test_result_is_in_bps_range(self) -> None:
        result = almgren_chriss_impact(
            quantity=10_000, adv=1_000_000, vol_daily=0.015, side="BUY"
        )
        self.assertLess(
            result, 1000, "Impact should be <1000 bps for normal participation"
        )


class TestAlphaDecayRegimes(unittest.TestCase):
    def test_low_vol_regime(self) -> None:
        from analytics.alpha_decay import classify_regime

        self.assertEqual(classify_regime(daily_vol_annualized_bps=60.0), "LOW")

    def test_medium_vol_regime(self) -> None:
        from analytics.alpha_decay import classify_regime

        self.assertEqual(classify_regime(daily_vol_annualized_bps=110.0), "MEDIUM")

    def test_high_vol_regime(self) -> None:
        from analytics.alpha_decay import classify_regime

        self.assertEqual(classify_regime(daily_vol_annualized_bps=200.0), "HIGH")

    def test_boundary_low_medium(self) -> None:
        from analytics.alpha_decay import classify_regime

        self.assertEqual(classify_regime(daily_vol_annualized_bps=80.0), "MEDIUM")

    def test_boundary_medium_high(self) -> None:
        from analytics.alpha_decay import classify_regime

        self.assertEqual(classify_regime(daily_vol_annualized_bps=150.0), "HIGH")


if __name__ == "__main__":
    unittest.main()
