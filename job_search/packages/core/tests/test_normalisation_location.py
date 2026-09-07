from __future__ import annotations

import unittest

from tests.fixtures.normalisation_examples import LOCATION_EXAMPLES

from core.normalisation.location import normalise_location


class TestNormaliseLocation(unittest.TestCase):
    """Tests against real location strings pulled from bronze — see this
    plan's scope note for what is and isn't resolved."""

    def test_real_bronze_examples(self) -> None:
        for raw, country_iso, region, is_remote in LOCATION_EXAMPLES:
            with self.subTest(raw=raw):
                result = normalise_location(raw)
                self.assertEqual(result.country_iso, country_iso)
                self.assertEqual(result.region, region)
                self.assertEqual(result.is_remote, is_remote)

    def test_none_location_is_fully_unresolved_and_does_not_raise(self) -> None:
        """int_jobs__unioned.location is nullable (manual entries with
        failed extraction), so the real caller can pass None."""
        result = normalise_location(None)
        self.assertIsNone(result.country_iso)
        self.assertIsNone(result.region)
        self.assertFalse(result.is_remote)
