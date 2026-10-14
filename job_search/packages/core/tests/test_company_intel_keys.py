"""Unit tests for company_key."""

from __future__ import annotations

import hashlib
import unittest

from core.company_intel.keys import company_key


class TestCompanyKey(unittest.TestCase):
    """company_key follows normalise_company then md5."""

    def test_it_is_the_md5_of_the_normalised_name(self) -> None:
        self.assertEqual(
            company_key("Stripe, Inc."), hashlib.md5(b"Stripe").hexdigest()
        )

    def test_legal_suffix_and_casing_do_not_change_the_key(self) -> None:
        self.assertEqual(company_key("ACME LTD"), company_key("Acme"))
        self.assertEqual(company_key("Acme Limited"), company_key("Acme"))

    def test_known_aliases_share_a_key(self) -> None:
        self.assertEqual(company_key("Facebook"), company_key("Meta"))

    def test_no_name_gives_no_key(self) -> None:
        self.assertIsNone(company_key(None))
        self.assertIsNone(company_key("   "))


if __name__ == "__main__":
    unittest.main()
