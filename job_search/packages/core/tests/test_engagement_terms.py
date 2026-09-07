from __future__ import annotations

import unittest

from core.enrichment.engagement_terms import (
    classify_engagement,
    extract_engagement_terms,
)


class TestClassifyEngagement(unittest.TestCase):
    """Tests for classify_engagement, against real bronze description text."""

    def test_outside_ir35_with_rate_and_duration(self) -> None:
        """Real Adzuna posting 5860498506."""
        result = classify_engagement(
            "Senior Data Engineer – Microsoft Fabric Contract: Outside IR35 "
            "Rate : £450 - £500 per day Start date: Expected 14 September "
            "2026 Duration: To 18 December 2026 Location : Onsite in London "
            "4 days/week"
        )
        self.assertEqual(result.engagement_type, "contract")
        self.assertEqual(result.ir35_status, "outside")
        self.assertEqual(result.engagement_vehicle, "unknown")

    def test_inside_ir35_case_insensitive(self) -> None:
        """Real Adzuna posting 5855376149 says 'INSIDE IR35'."""
        result = classify_engagement(
            "6 month contract, INSIDE IR35, hybrid working in Manchester."
        )
        self.assertEqual(result.engagement_type, "contract")
        self.assertEqual(result.ir35_status, "inside")

    def test_umbrella_sets_vehicle_but_leaves_ir35_undetermined(self) -> None:
        """Real phrasing pattern: 'umbrella' with no explicit IR35 status."""
        result = classify_engagement(
            "Contract role via umbrella company, London based, 3 months."
        )
        self.assertEqual(result.engagement_type, "contract")
        self.assertEqual(result.engagement_vehicle, "umbrella")
        self.assertEqual(result.ir35_status, "undetermined")

    def test_paye_sets_vehicle(self) -> None:
        """Real phrasing pattern from bronze: bare 'PAYE'."""
        result = classify_engagement("Contract, PAYE only, 12 months.")
        self.assertEqual(result.engagement_vehicle, "paye")

    def test_agency_paye_is_distinguished_from_plain_paye(self) -> None:
        result = classify_engagement("Rate via agency PAYE, inside IR35.")
        self.assertEqual(result.engagement_vehicle, "agency_paye")

    def test_limited_company_sets_vehicle(self) -> None:
        result = classify_engagement(
            "Outside IR35, must operate via your own limited company."
        )
        self.assertEqual(result.engagement_vehicle, "limited")

    def test_bare_ir35_mention_is_undetermined(self) -> None:
        """Real bronze rows (e.g. 5858256031) mention IR35 with no inside/
        outside qualifier, in a description truncated to 500 chars."""
        result = classify_engagement(
            "Contract Data Engineer needed. Must understand IR35 "
            "implications for this role."
        )
        self.assertEqual(result.ir35_status, "undetermined")

    def test_permanent_role_is_not_applicable_for_ir35(self) -> None:
        result = classify_engagement(
            "Permanent Data Engineer role, London, hybrid, £70,000."
        )
        self.assertEqual(result.engagement_type, "permanent")
        self.assertEqual(result.ir35_status, "not_applicable")
        self.assertEqual(result.engagement_vehicle, "unknown")

    def test_explicit_ir35_outranks_an_incidental_permanent_match(self) -> None:
        """Regression: 'permanent' used to unconditionally win.

        A real contract posting can say "permanent" about something else
        entirely (benefits, headcount). An explicit inside/outside IR35
        statement is far stronger evidence of a contract engagement, so
        it must disqualify the permanent branch — otherwise the stated
        IR35 status is thrown away and replaced with 'not_applicable'.
        """
        result = classify_engagement(
            "Contract, outside IR35. We offer permanent health insurance."
        )
        self.assertEqual(result.engagement_type, "contract")
        self.assertEqual(result.ir35_status, "outside")

    def test_explicit_inside_ir35_also_outranks_permanent(self) -> None:
        result = classify_engagement(
            "6 month engagement, inside IR35. Permanent staff discounts apply."
        )
        self.assertEqual(result.engagement_type, "contract")
        self.assertEqual(result.ir35_status, "inside")

    def test_ftc_with_stated_ir35_stays_ftc_not_contract(self) -> None:
        """The IR35 guard only disqualifies `permanent` — it must not
        flatten a more specific ftc/interim signal into 'contract'."""
        result = classify_engagement("12-month FTC, inside IR35, NHS pension.")
        self.assertEqual(result.engagement_type, "ftc")
        self.assertEqual(result.ir35_status, "inside")

    def test_fixed_term_contract_is_ftc(self) -> None:
        result = classify_engagement(
            "12-month FTC, Data Engineer, band 6, NHS pension."
        )
        self.assertEqual(result.engagement_type, "ftc")

    def test_interim_is_its_own_engagement_type(self) -> None:
        result = classify_engagement(
            "Interim Head of Data required for a 3-month engagement."
        )
        self.assertEqual(result.engagement_type, "interim")

    def test_no_signal_at_all_is_unknown_not_permanent(self) -> None:
        """No engagement-type language at all — never default to permanent."""
        result = classify_engagement(
            "We are looking for a talented engineer to join our team."
        )
        self.assertEqual(result.engagement_type, "unknown")
        self.assertEqual(result.ir35_status, "unknown")
        self.assertEqual(result.engagement_vehicle, "unknown")

    def test_none_description_is_unknown(self) -> None:
        """Manual entries and some sources can have a null description."""
        result = classify_engagement(None)
        self.assertEqual(result.engagement_type, "unknown")
        self.assertEqual(result.ir35_status, "unknown")
        self.assertEqual(result.engagement_vehicle, "unknown")


class TestExtractEngagementTerms(unittest.TestCase):
    """Tests for extract_engagement_terms's rate/duration parsing, against
    real bronze salary_raw and description text."""

    def test_day_rate_phrase_in_description_wins_over_salary_raw(self) -> None:
        """Real Adzuna posting 5860498506 — salary_raw is Adzuna's own
        pre-annualised 117000-130000, but the description states the true
        day rate explicitly; the phrase is authoritative for rate_basis."""
        result = extract_engagement_terms(
            "Senior Data Engineer – Microsoft Fabric Contract: Outside IR35 "
            "Rate : £450 - £500 per day",
            salary_raw="117000-130000",
        )
        self.assertEqual(result.rate_basis, "daily")
        self.assertEqual(result.rate_currency, "GBP")
        self.assertEqual(result.rate_daily_equivalent, 475)
        self.assertEqual(result.rate_annualised, 475 * 260)

    def test_plain_annual_salary_raw_with_no_rate_phrase(self) -> None:
        """Real Adzuna posting 5510354959 — no rate phrase, plain
        pre-annualised salary_raw numbers."""
        result = extract_engagement_terms(
            "Data Engineer, permanent role, London.", salary_raw="130000-130000"
        )
        self.assertEqual(result.rate_basis, "annual")
        self.assertEqual(result.rate_annualised, 130000)
        self.assertEqual(result.rate_daily_equivalent, 130000 / 260)

    def test_jooble_k_shorthand_annual_range(self) -> None:
        """Real Jooble salary text: '£80k - £95k per year'."""
        result = extract_engagement_terms(None, salary_raw="£80k - £95k per year")
        self.assertEqual(result.rate_basis, "annual")
        self.assertEqual(result.rate_currency, "GBP")
        self.assertEqual(result.rate_annualised, 87500)

    def test_monthly_rate_annualises_via_times_twelve(self) -> None:
        """Real Jooble salary text: '£1,500 per month' — 'monthly' isn't a
        named rate_basis; it collapses into 'annual' (see plan scope note)."""
        result = extract_engagement_terms(None, salary_raw="£1,500 per month")
        self.assertEqual(result.rate_basis, "annual")
        self.assertEqual(result.rate_annualised, 1500 * 12)

    def test_hourly_rate_with_dollar_currency(self) -> None:
        """Real Jooble salary text: '$15 per hour'."""
        result = extract_engagement_terms(None, salary_raw="$15 per hour")
        self.assertEqual(result.rate_basis, "hourly")
        self.assertEqual(result.rate_currency, "USD")
        self.assertEqual(result.rate_daily_equivalent, 15 * 7.5)
        self.assertEqual(result.rate_annualised, 15 * 7.5 * 260)

    def test_no_salary_at_all_is_unknown_basis_with_null_figures(self) -> None:
        """Real Greenhouse rows: salary_raw is always NULL."""
        result = extract_engagement_terms("Senior Data Engineer, Public Sector", None)
        self.assertEqual(result.rate_basis, "unknown")
        self.assertIsNone(result.rate_annualised)
        self.assertIsNone(result.rate_daily_equivalent)
        self.assertIsNone(result.rate_currency)

    def test_contract_length_in_months_extracted(self) -> None:
        result = extract_engagement_terms(
            "6 month contract, inside IR35, hybrid working.", salary_raw=None
        )
        self.assertEqual(result.contract_length_months, 6)

    def test_no_duration_stated_is_none_not_zero(self) -> None:
        result = extract_engagement_terms(
            "Contract role, outside IR35.", salary_raw=None
        )
        self.assertIsNone(result.contract_length_months)

    def test_extension_likely_phrase(self) -> None:
        result = extract_engagement_terms(
            "6 month contract with a view to extend.", salary_raw=None
        )
        self.assertEqual(result.extension_likelihood, "likely")

    def test_extension_possible_phrase(self) -> None:
        result = extract_engagement_terms(
            "3 month contract, possible extension subject to budget.",
            salary_raw=None,
        )
        self.assertEqual(result.extension_likelihood, "possible")

    def test_extension_unlikely_phrase(self) -> None:
        result = extract_engagement_terms(
            "Fixed 6 month contract, no extension available.", salary_raw=None
        )
        self.assertEqual(result.extension_likelihood, "unlikely")

    def test_no_extension_language_is_unstated(self) -> None:
        result = extract_engagement_terms("Permanent role, London.", salary_raw=None)
        self.assertEqual(result.extension_likelihood, "unstated")

    def test_comma_only_salary_raw_does_not_crash(self) -> None:
        """Regression: salary_raw with punctuation but no number.

        The number regex used to be `[\\d,]+`, which matched the bare ","
        in "Competitive, negotiable" as a whole token; _parse_amount then
        evaluated float("") and raised ValueError. Because
        write_engagement_terms runs the whole batch inside one
        engine.begin() with no per-row handling, that single row would
        have aborted the entire enrichment run.
        """
        result = extract_engagement_terms(None, salary_raw="Competitive, negotiable")
        self.assertEqual(result.rate_basis, "unknown")
        self.assertIsNone(result.rate_annualised)
        self.assertIsNone(result.rate_daily_equivalent)
        self.assertIsNone(result.rate_currency)

    def test_free_text_salary_raw_with_real_numbers_does_not_crash(self) -> None:
        """Same regression, for free text that DOES contain numbers.

        Known limitation, deliberately not fixed here: the salary_raw
        fallback averages every number it finds, so the unrelated
        "25 days holiday" drags the figure down to the mean of 50000 and
        25. Telling a salary apart from unrelated numbers in free text is
        a separate problem (the LLM-residual pass PLAN.md defers); this
        test pins only that the row is processed without raising.
        """
        result = extract_engagement_terms(
            None, salary_raw="50000 plus bonus, 25 days holiday"
        )
        self.assertEqual(result.rate_basis, "annual")
        self.assertEqual(result.rate_annualised, (50000 + 25) / 2)

    def test_classification_fields_pass_through(self) -> None:
        """extract_engagement_terms includes classify_engagement's fields."""
        result = extract_engagement_terms(
            "Permanent Data Engineer role, London.", salary_raw="70000-70000"
        )
        self.assertEqual(result.engagement_type, "permanent")
        self.assertEqual(result.ir35_status, "not_applicable")
