from __future__ import annotations

import unittest

from core.llm.prompts import load_prompt


class TestLoadPrompt(unittest.TestCase):
    def test_loads_the_real_job_categorisation_claude_v1_prompt(self) -> None:
        text = load_prompt("job_categorisation", "claude", 1)
        self.assertIn("{categories}", text)
        self.assertIn("{title}", text)

    def test_raises_file_not_found_for_a_missing_prompt(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_prompt("does_not_exist", "claude", 1)


if __name__ == "__main__":
    unittest.main()
