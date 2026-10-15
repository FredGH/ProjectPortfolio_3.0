"""Unit tests for the evidence pack."""

from __future__ import annotations

import unittest

from tests.artefacts_fixtures import cv_ref, make_pack
from tests.company_intel_fixtures import make_job
from tests.tailoring_fixtures import make_truth_base

from core.artefacts.context import build_pack


class TestBuildPack(unittest.TestCase):
    """build_pack names every piece of evidence with a citable id."""

    def test_cv_job_and_site_evidence_all_get_ids(self) -> None:
        pack = make_pack()
        ids = set(pack.items)
        self.assertIn("cv:summary", ids)
        self.assertIn("cv:role:0", ids)
        self.assertIn(cv_ref(0, 0), ids)
        self.assertIn("job:posting", ids)
        self.assertIn("job:skill:dbt", ids)
        self.assertIn("site:business_model:0", ids)
        self.assertIn("site:tech_stack:0", ids)
        self.assertTrue(pack.has_site)

    def test_a_role_is_cited_as_title_company_and_dates(self) -> None:
        text = make_pack().items["cv:role:0"].text
        self.assertEqual(text, "Senior Data Engineer at Acme Bank (2019-01 – present)")

    def test_no_site_facts_means_no_site_ids(self) -> None:
        pack = make_pack(site_facts=[])
        self.assertFalse(pack.has_site)
        self.assertFalse([i for i in pack.items if i.startswith("site:")])

    def test_the_header_fields_come_from_the_cv_and_the_job(self) -> None:
        pack = make_pack()
        self.assertEqual(pack.identity, "Zz Fixture")
        self.assertEqual(pack.contact, "zz@example.com")
        self.assertEqual(
            (pack.title, pack.company), ("Data Engineer", "Zzfixture Intel Ltd")
        )

    def test_the_posting_is_capped(self) -> None:
        job = make_job(description="x" * 20000)
        pack = build_pack(make_truth_base(), job)
        self.assertLessEqual(len(pack.items["job:posting"].text), 6000)

    def test_texts_returns_the_text_of_known_ids_only(self) -> None:
        pack = make_pack()
        self.assertEqual(
            pack.texts([cv_ref(0, 0), "cv:nope"]),
            ["Built dbt models for risk reporting"],
        )


if __name__ == "__main__":
    unittest.main()
