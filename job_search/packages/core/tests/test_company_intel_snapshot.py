"""Unit tests for the company snapshot (model-knowledge facts table)."""

from __future__ import annotations

import json
import unittest

from tests.company_intel_fixtures import FakeAdapter

from core.company_intel.schema import SNAPSHOT_KEYS, IntelContent, IntelFact
from core.company_intel.snapshot import SnapshotError, build_snapshot, guard_snapshot

_NOT_KNOWN = "not known"


def _row(value: str, as_of: str | None = "2024") -> dict:
    """Build one raw snapshot row.

    Args:
        value: The row's value.
        as_of: The year it refers to.

    Returns:
        The dict the model is asked to return per metric.
    """
    return {"value": value, "as_of": as_of}


class TestGuardSnapshot(unittest.TestCase):
    """guard_snapshot always returns the six rows, in order, and never invents."""

    def test_it_returns_all_six_rows_in_the_fixed_order(self) -> None:
        raw = {key: _row(f"v-{key}") for key in reversed(SNAPSHOT_KEYS)}
        items = guard_snapshot(raw)
        self.assertEqual([i.key for i in items], list(SNAPSHOT_KEYS))
        self.assertEqual(items[0].value, f"v-{SNAPSHOT_KEYS[0]}")
        self.assertEqual(items[0].as_of, "2024")

    def test_missing_blank_and_non_text_values_become_not_known(self) -> None:
        raw = {
            "industry": _row("Payments"),
            "ranking": _row("   "),
            "market_share": {"value": None},
            "growth_5y": {"value": 12},
            "employees": "about 8,000",
        }
        items = {i.key: i for i in guard_snapshot(raw)}
        self.assertEqual(items["industry"].value, "Payments")
        for key in ("ranking", "market_share", "growth_5y", "satisfaction"):
            self.assertEqual(items[key].value, _NOT_KNOWN, key)
            self.assertIsNone(items[key].as_of, key)
        # A bare string instead of {value, as_of} is accepted as the value.
        self.assertEqual(items["employees"].value, "about 8,000")

    def test_not_known_answers_in_other_words_are_normalised(self) -> None:
        raw = {
            key: _row(text)
            for key, text in zip(
                SNAPSHOT_KEYS,
                ["Unknown", "N/A", "none", "I don't know", "-", "Not known"],
            )
        }
        self.assertTrue(all(i.value == _NOT_KNOWN for i in guard_snapshot(raw)))

    def test_a_long_value_is_cut_and_junk_input_gives_six_not_known_rows(self) -> None:
        long = guard_snapshot({"industry": _row("x" * 500)})[0]
        self.assertLessEqual(len(long.value), 200)
        for junk in (None, "oops", [], 5):
            items = guard_snapshot(junk)
            self.assertEqual(len(items), 6)
            self.assertTrue(all(i.value == _NOT_KNOWN for i in items))

    def test_an_as_of_that_is_not_a_short_string_is_dropped(self) -> None:
        items = {
            i.key: i
            for i in guard_snapshot(
                {
                    "industry": _row("A", as_of="2024"),
                    "ranking": _row("B", as_of="x" * 80),
                    "employees": {"value": "C", "as_of": 2023},
                }
            )
        }
        self.assertEqual(items["industry"].as_of, "2024")
        self.assertIsNone(items["ranking"].as_of)
        self.assertEqual(items["employees"].as_of, "2023")


class TestBuildSnapshot(unittest.TestCase):
    """build_snapshot asks the model with the crawled facts as context."""

    def _content(self) -> IntelContent:
        """Build a small crawled profile.

        Returns:
            Intel content with a business-model and a size fact.
        """
        return IntelContent(
            business_model=[
                IntelFact(text="Sells widgets {to} retailers.", source_url="u")
            ],
            size_signals=[
                IntelFact(text="About 9,000 staff worldwide.", source_url="u")
            ],
        )

    def test_it_returns_guarded_rows_and_usage(self) -> None:
        reply = json.dumps(
            {
                "industry": _row("Industrial widgets"),
                "employees": _row("~9,000", "2024"),
            }
        )
        adapter = FakeAdapter([reply])
        result = build_snapshot(
            "Acme", self._content(), adapters={"anthropic": adapter}
        )
        self.assertEqual(len(result.items), 6)
        self.assertEqual(result.items[0].value, "Industrial widgets")
        self.assertEqual(result.prompt_version, "claude.v1")
        self.assertEqual(result.calls[0]["task"], "company_snapshot")

    def test_the_prompt_carries_the_company_and_the_crawled_facts_intact(self) -> None:
        adapter = FakeAdapter(["{}"])
        build_snapshot("Acme Ltd", self._content(), adapters={"anthropic": adapter})
        prompt = adapter.prompts[0]
        self.assertIn("Acme Ltd", prompt)
        self.assertIn("Sells widgets {to} retailers.", prompt)
        self.assertIn("About 9,000 staff worldwide.", prompt)

    def test_an_unreadable_reply_is_an_error_that_still_carries_its_cost(self) -> None:
        with self.assertRaises(SnapshotError) as ctx:
            build_snapshot(
                "Acme", IntelContent(), adapters={"anthropic": FakeAdapter(["no json"])}
            )
        self.assertEqual(ctx.exception.calls[0]["task"], "company_snapshot")

    def test_an_empty_profile_still_works(self) -> None:
        adapter = FakeAdapter(["{}"])
        result = build_snapshot("Acme", IntelContent(), adapters={"anthropic": adapter})
        self.assertTrue(all(i.value == _NOT_KNOWN for i in result.items))


class TestIsEmptyIgnoresTheSnapshot(unittest.TestCase):
    """The snapshot is model knowledge, so it never makes a profile 'non-empty'."""

    def test_a_profile_with_only_a_snapshot_is_still_empty(self) -> None:
        content = IntelContent(snapshot=guard_snapshot({"industry": _row("A")}))
        self.assertTrue(content.is_empty())


if __name__ == "__main__":
    unittest.main()
