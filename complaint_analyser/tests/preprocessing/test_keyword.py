from __future__ import annotations

import unittest
from pathlib import Path

from agentic_triage.preprocessing.keyword import build_keyword_processor, extract_keywords

KEYWORDS_PATH = str(
    Path(__file__).parent.parent.parent / "domains" / "banking_complaints" / "keywords.txt"
)


class TestBuildKeywordProcessor(unittest.TestCase):
    def test_loads_from_file(self):
        processor = build_keyword_processor(KEYWORDS_PATH)
        self.assertIsNotNone(processor)

    def test_handles_nonexistent_file(self):
        processor = build_keyword_processor("/nonexistent/keywords.txt")
        result = extract_keywords("GDPR breach", processor)
        self.assertEqual(result, [])


class TestExtractKeywords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.processor = build_keyword_processor(KEYWORDS_PATH)

    def test_extracts_known_keyword(self):
        result = extract_keywords("There was a GDPR breach in my account", self.processor)
        self.assertIn("GDPR", result)

    def test_case_insensitive(self):
        result = extract_keywords("there was a gdpr breach", self.processor)
        self.assertTrue(any(k.upper() == "GDPR" for k in result))

    def test_extracts_multi_word_keyword(self):
        result = extract_keywords("I noticed an unauthorised transaction", self.processor)
        self.assertTrue(any("unauthorised" in k.lower() for k in result))

    def test_no_duplicates(self):
        result = extract_keywords("GDPR violation and GDPR exposure", self.processor)
        self.assertEqual(len(result), len(set(result)))

    def test_no_match_returns_empty(self):
        result = extract_keywords("the weather is nice today", self.processor)
        self.assertEqual(result, [])

    def test_multiple_keywords_detected(self):
        result = extract_keywords("GDPR breach involving fraud and FCA violation", self.processor)
        self.assertGreaterEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
