from __future__ import annotations

import unittest

from core.enrichment.engagement_terms import classify_engagement


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
