"""The shared theme must keep plain-text elements from overflowing."""

from __future__ import annotations

import unittest

from core.ui.theme import _CSS


class TestTheme(unittest.TestCase):
    def test_plain_text_wraps_instead_of_running_off_the_page(self) -> None:
        block = _CSS.split('[data-testid="stText"]')[1].split("}")[0]
        self.assertIn("pre-wrap", block)
        self.assertIn("overflow-wrap", block)


if __name__ == "__main__":
    unittest.main()
