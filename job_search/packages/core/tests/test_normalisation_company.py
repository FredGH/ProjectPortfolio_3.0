from __future__ import annotations

import unittest

from tests.fixtures.normalisation_examples import COMPANY_EXAMPLES

from core.normalisation.company import normalise_company


class TestNormaliseCompany(unittest.TestCase):
    """Tests against real company names pulled from bronze."""

    def test_real_bronze_examples(self) -> None:
        for source_name, raw, expected in COMPANY_EXAMPLES:
            with self.subTest(source=source_name, raw=raw):
                self.assertEqual(normalise_company(raw), expected)

    def test_facebook_aliases_to_meta(self) -> None:
        self.assertEqual(normalise_company("Facebook"), "Meta")

    def test_alphabet_aliases_to_google(self) -> None:
        self.assertEqual(normalise_company("Alphabet Inc"), "Google")

    def test_alias_is_case_insensitive(self) -> None:
        self.assertEqual(normalise_company("FACEBOOK"), "Meta")

    def test_none_company_returns_none_and_does_not_raise(self) -> None:
        """int_jobs__unioned.company is nullable (manual entries with
        failed extraction), so the real caller can pass None."""
        self.assertIsNone(normalise_company(None))
