from __future__ import annotations

import unittest

from core.classification.seniority import derive_seniority_band


class TestDeriveSeniorityBand(unittest.TestCase):
    def test_none_title_defaults_to_mid(self) -> None:
        self.assertEqual(derive_seniority_band(None), "mid")

    def test_no_seniority_term_defaults_to_mid(self) -> None:
        self.assertEqual(derive_seniority_band("Data Engineer"), "mid")

    def test_senior_and_sr(self) -> None:
        self.assertEqual(derive_seniority_band("Senior Data Engineer"), "senior")
        self.assertEqual(derive_seniority_band("Sr Data Engineer"), "senior")

    def test_junior_variants(self) -> None:
        for title in (
            "Junior Data Engineer",
            "Jr Data Engineer",
            "Graduate Data Engineer",
            "Associate Data Engineer",
        ):
            self.assertEqual(derive_seniority_band(title), "junior")

    def test_lead(self) -> None:
        self.assertEqual(derive_seniority_band("Lead Data Engineer"), "lead")

    def test_principal_and_staff(self) -> None:
        # "Staff" folds into "principal" — no separate tier in PLAN.md's
        # 5-value taxonomy; industry convention treats staff+ as
        # roughly principal-equivalent. A disclosed judgment call.
        self.assertEqual(derive_seniority_band("Principal Data Engineer"), "principal")
        self.assertEqual(derive_seniority_band("Staff Data Engineer"), "principal")

    def test_case_insensitive(self) -> None:
        self.assertEqual(derive_seniority_band("SENIOR DATA ENGINEER"), "senior")
