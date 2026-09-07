from __future__ import annotations

import unittest

from tests.fixtures.normalisation_examples import SALARY_EXAMPLES

from core.normalisation.salary import parse_salary


class TestParseSalary(unittest.TestCase):
    """Tests against real salary/description text pulled from bronze."""

    def test_real_bronze_examples(self) -> None:
        for description, salary_raw, expected_annualised in SALARY_EXAMPLES:
            with self.subTest(description=description, salary_raw=salary_raw):
                result = parse_salary(description, salary_raw)
                self.assertEqual(result.annualised_gbp, expected_annualised)

    def test_band_is_ten_k_wide(self) -> None:
        result = parse_salary(None, "£80k - £95k per year")
        self.assertEqual(result.band, "80000-90000")

    def test_no_salary_gives_no_band(self) -> None:
        result = parse_salary("Senior Data Engineer, Public Sector", None)
        self.assertIsNone(result.band)
        self.assertIsNone(result.annualised_gbp)
        self.assertEqual(result.rate_basis, "unknown")

    def test_usd_hourly_rate_converts_to_gbp(self) -> None:
        """Real Jooble salary text: '$15 per hour'. 15 * 7.5 * 260 = 29250
        USD, converted at this module's documented approximate USD/GBP rate."""
        result = parse_salary(None, "$15 per hour")
        self.assertEqual(result.original_currency, "USD")
        self.assertAlmostEqual(result.annualised_gbp, 29250 * 0.79)
