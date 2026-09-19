"""compute_bullet_id must be deterministic and position/content-sensitive."""

from __future__ import annotations

import unittest

from core.cv.bullet_id import compute_bullet_id


class TestComputeBulletId(unittest.TestCase):
    def test_same_text_and_position_yields_the_same_id(self) -> None:
        first = compute_bullet_id(0, "Led a team of 3 data engineers.")
        second = compute_bullet_id(0, "Led a team of 3 data engineers.")
        self.assertEqual(first, second)

    def test_different_position_yields_a_different_id(self) -> None:
        same_text = "Led a team of 3 data engineers."
        self.assertNotEqual(
            compute_bullet_id(0, same_text), compute_bullet_id(1, same_text)
        )

    def test_different_text_yields_a_different_id(self) -> None:
        self.assertNotEqual(
            compute_bullet_id(0, "Led a team of 3 data engineers."),
            compute_bullet_id(0, "Led a team of 5 data engineers."),
        )

    def test_whitespace_and_case_differences_do_not_change_the_id(self) -> None:
        first = compute_bullet_id(0, "Led   a team of 3 data engineers.")
        second = compute_bullet_id(0, "led a team of 3 data engineers.")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
