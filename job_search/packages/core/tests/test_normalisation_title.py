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
