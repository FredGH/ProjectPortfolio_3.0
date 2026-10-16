"""Unit tests for the UK take-home estimate (hand-checked numbers)."""

from __future__ import annotations

import unittest

from core.offers.tax import (
    corporation_tax,
    employee_ni,
    employer_ni,
    income_tax,
    inside_ir35_net,
    load_tax_config,
    net_for,
    outside_ir35_net,
)

_CFG = load_tax_config()


class TestBuildingBlocks(unittest.TestCase):
    """Each rule against a number worked by hand."""

    def test_income_tax_basic_rate_only(self) -> None:
        self.assertAlmostEqual(income_tax(30000, 0, _CFG), (30000 - 12570) * 0.20)

    def test_income_tax_crosses_into_the_higher_rate(self) -> None:
        # 60,000: 37,700 at 20% + (60,000 - 12,570 - 37,700) at 40%
        self.assertAlmostEqual(income_tax(60000, 0, _CFG), 37700 * 0.2 + 9730 * 0.4)

    def test_the_personal_allowance_tapers_above_100k(self) -> None:
        # allowance 12,570 - 10,000/2 = 7,570; taxable 102,430
        expected = 37700 * 0.2 + (102430 - 37700) * 0.4
        self.assertAlmostEqual(income_tax(110000, 0, _CFG), expected)

    def test_dividends_after_a_salary_at_the_allowance(self) -> None:
        # 500 free; to 37,700 at 8.75%; the last 2,300 at 33.75%
        expected = (37700 - 500) * 0.0875 + 2300 * 0.3375
        self.assertAlmostEqual(income_tax(12570, 40000, _CFG), expected)
        self.assertAlmostEqual(expected, 4031.25)

    def test_dividends_use_a_leftover_personal_allowance(self) -> None:
        # salary 0: the allowance covers 12,570 of the 20,000 dividends
        # remaining 7,430: 500 free, 6,930 at 8.75%
        self.assertAlmostEqual(income_tax(0, 20000, _CFG), 6930 * 0.0875)

    def test_employee_ni(self) -> None:
        self.assertAlmostEqual(employee_ni(30000, _CFG), (30000 - 12570) * 0.08)
        self.assertAlmostEqual(employee_ni(60000, _CFG), 37700 * 0.08 + 9730 * 0.02)
        self.assertEqual(employee_ni(10000, _CFG), 0.0)

    def test_employer_ni(self) -> None:
        self.assertAlmostEqual(employer_ni(30000, _CFG), 25000 * 0.15)
        self.assertEqual(employer_ni(4000, _CFG), 0.0)

    def test_corporation_tax_bands_and_continuity(self) -> None:
        self.assertAlmostEqual(corporation_tax(40000, _CFG), 7600)
        self.assertAlmostEqual(
            corporation_tax(100000, _CFG), 22750
        )  # 25% - 3/200 x 150k
        self.assertAlmostEqual(corporation_tax(300000, _CFG), 75000)
        self.assertAlmostEqual(corporation_tax(50000, _CFG), 9500)
        self.assertAlmostEqual(corporation_tax(250000, _CFG), 62500)
        self.assertEqual(corporation_tax(-5, _CFG), 0.0)


class TestNet(unittest.TestCase):
    """The inside / outside estimates behave sensibly."""

    def test_the_umbrella_invoice_is_fully_accounted_for(self) -> None:
        net = inside_ir35_net(500, _CFG)
        steps = dict(net.steps)
        gross_pay = net.net_year + steps["Income tax (PAYE)"] + steps["Employee NI"]
        rebuilt = (
            gross_pay
            + steps["Employer NI"]
            + steps["Apprenticeship levy"]
            + steps["Umbrella fee"]
        )
        self.assertAlmostEqual(rebuilt, net.gross_year, places=2)

    def test_outside_ir35_takes_home_more_than_inside_at_the_same_rate(self) -> None:
        for rate in (350, 500, 650, 800):
            self.assertGreater(
                outside_ir35_net(rate, _CFG).net_year,
                inside_ir35_net(rate, _CFG).net_year,
                rate,
            )

    def test_take_home_rises_with_the_rate_and_stays_below_it(self) -> None:
        for status in ("inside", "outside"):
            nets = [net_for(r, status, _CFG) for r in (300, 400, 500, 600, 700)]
            self.assertEqual([n.net_day for n in nets], sorted(n.net_day for n in nets))
            self.assertTrue(all(n.net_day < n.day_rate for n in nets))

    def test_an_inside_offer_needs_a_higher_rate_to_match_an_outside_one(self) -> None:
        outside = outside_ir35_net(500, _CFG).net_year
        self.assertLess(inside_ir35_net(500, _CFG).net_year, outside)
        self.assertGreater(
            inside_ir35_net(600, _CFG).net_year, inside_ir35_net(500, _CFG).net_year
        )

    def test_the_result_states_its_assumptions_and_that_the_config_is_unverified(
        self,
    ) -> None:
        net = outside_ir35_net(500, _CFG)
        self.assertEqual(net.tax_year, "2025-26")
        self.assertFalse(net.verified)
        self.assertTrue(net.assumptions)
        self.assertTrue(any("220" in a for a in net.assumptions))

    def test_an_undetermined_status_cannot_be_compared(self) -> None:
        for status in ("undetermined", "", "maybe"):
            with self.assertRaises(ValueError):
                net_for(500, status, _CFG)


if __name__ == "__main__":
    unittest.main()
