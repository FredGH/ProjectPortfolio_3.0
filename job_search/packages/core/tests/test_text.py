from __future__ import annotations

import unittest

from core.text import readable_description


class TestReadableDescription(unittest.TestCase):
    def test_strips_html_tags(self) -> None:
        self.assertEqual(
            readable_description("<p>Hello <b>world</b></p>"), "Hello world"
        )

    def test_decodes_entities_including_double_escaped_ones(self) -> None:
        # Real ATS payloads store this HTML-entity-escaped, i.e. the
        # literal text "&amp;lt;" rather than a raw "<" or even "&lt;" —
        # both unescape passes are needed to reach plain text.
        self.assertEqual(readable_description("Coffee &amp;amp; tea"), "Coffee & tea")

    def test_collapses_repeated_blank_lines(self) -> None:
        self.assertEqual(
            readable_description("Line one\n\n\n\nLine two"),
            "Line one\n\nLine two",
        )

    def test_collapses_repeated_spaces_and_tabs(self) -> None:
        self.assertEqual(readable_description("a   b\t\tc"), "a b c")

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        self.assertEqual(readable_description("  <p>hi</p>  "), "hi")


if __name__ == "__main__":
    unittest.main()
