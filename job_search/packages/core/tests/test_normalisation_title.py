from __future__ import annotations

import unittest

from tests.fixtures.normalisation_examples import TITLE_EXAMPLES

from core.normalisation.title import strip_title, title_for_display, title_raw


class TestTitleFunctions(unittest.TestCase):
    """Tests against real job titles pulled from bronze, plus two
    synthetic cases for patterns PLAN.md names explicitly but that don't
    appear in this session's own bronze sample (m/f/d, req IDs)."""

    def test_real_and_spec_named_examples(self) -> None:
        for raw, expected_strip, expected_display in TITLE_EXAMPLES:
            with self.subTest(raw=raw, kind="strip_title"):
                self.assertEqual(strip_title(raw), expected_strip)
            with self.subTest(raw=raw, kind="title_for_display"):
                self.assertEqual(title_for_display(raw), expected_display)

    def test_title_raw_is_always_verbatim(self) -> None:
        for raw, _, _ in TITLE_EXAMPLES:
            with self.subTest(raw=raw):
                self.assertEqual(title_raw(raw), raw)

    def test_strip_title_removes_seniority_but_display_keeps_it(self) -> None:
        """The trap DECISIONS.md §5 warns about: never reuse strip_title's
        output for a generated document."""
        raw = "Senior Data Engineer"
        self.assertNotEqual(strip_title(raw), title_for_display(raw))
        self.assertIn("Senior", title_for_display(raw))
        self.assertNotIn("Senior", strip_title(raw))

    def test_none_title_returns_none_from_every_function(self) -> None:
        """int_jobs__unioned.title is nullable (manual entries with
        failed extraction), so the real caller can pass None."""
        self.assertIsNone(title_raw(None))
        self.assertIsNone(strip_title(None))
        self.assertIsNone(title_for_display(None))

    def test_location_suffixes_are_deliberately_not_stripped(self) -> None:
        """Pins this module's documented scope note: only the work-mode
        suffix is removed. A trailing place name stays, because the same
        "- <words>" shape also covers legitimate qualifiers, which a
        generic rule would silently mangle."""
        self.assertEqual(
            title_for_display("Data Engineer - Manchester"),
            "Data Engineer - Manchester",
        )
        self.assertEqual(
            title_for_display("Software Engineer - Data Engineering"),
            "Software Engineer - Data Engineering",
        )
        self.assertEqual(title_for_display("Data Engineer - Remote"), "Data Engineer")
