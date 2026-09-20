"""Unit tests for the pure helpers in core.skills.review."""

from __future__ import annotations

import unittest

from core.skills.review import is_suspicious_label_match


class TestIsSuspiciousLabelMatch(unittest.TestCase):
    """A label match is suspicious when the string does not name the skill."""

    def test_the_skills_own_name_is_not_suspicious(self) -> None:
        self.assertFalse(is_suspicious_label_match("sql", "SQL"))
        self.assertFalse(is_suspicious_label_match("communication", "communication"))

    def test_an_esco_qualifier_on_the_label_is_ignored(self) -> None:
        # ESCO writes "Python (computer programming)"; the string "python"
        # is that skill's name, not a hidden-label accident.
        self.assertFalse(
            is_suspicious_label_match("python", "Python (computer programming)")
        )
        self.assertFalse(
            is_suspicious_label_match("java", "Java (computer programming)")
        )

    def test_a_qualifier_on_the_string_is_ignored(self) -> None:
        self.assertFalse(is_suspicious_label_match("mysql (rds", "MySQL"))

    def test_a_tool_filed_under_a_broad_skill_is_suspicious(self) -> None:
        self.assertTrue(is_suspicious_label_match("kotlin", "computer programming"))
        self.assertTrue(
            is_suspicious_label_match("numpy", "software components libraries")
        )
        self.assertTrue(
            is_suspicious_label_match("tableau", "data visualisation software")
        )

    def test_a_similar_but_different_skill_is_suspicious(self) -> None:
        self.assertTrue(is_suspicious_label_match("visual studio", "Visual Basic"))
        self.assertTrue(
            is_suspicious_label_match("change management", "apply change management")
        )

    def test_a_qualified_string_whose_head_is_another_skill_is_suspicious(
        self,
    ) -> None:
        self.assertTrue(
            is_suspicious_label_match(
                "agile (kanban/scrum", "ICT project management methodology"
            )
        )

    def test_a_missing_label_cannot_be_judged_so_is_not_flagged(self) -> None:
        self.assertFalse(is_suspicious_label_match("kotlin", None))


if __name__ == "__main__":
    unittest.main()
