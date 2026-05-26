from __future__ import annotations

import unittest

from agentic_triage.preprocessing.normalizer import build_symspell, normalize


class TestBuildSymspell(unittest.TestCase):
    def test_returns_symspell_instance(self):
        from symspellpy import SymSpell
        sym_spell = build_symspell()
        self.assertIsInstance(sym_spell, SymSpell)

    def test_accepts_nonexistent_vocab_path(self):
        sym_spell = build_symspell(vocab_path="/nonexistent/path.txt")
        self.assertIsNotNone(sym_spell)


class TestNormalize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sym_spell = build_symspell()

    def test_returns_string(self):
        result = normalize("I have a complaint", self.sym_spell)
        self.assertIsInstance(result, str)

    def test_non_empty_output(self):
        result = normalize("I have a complaint about my bank", self.sym_spell)
        self.assertGreater(len(result), 0)

    def test_empty_string(self):
        result = normalize("", self.sym_spell)
        self.assertIsInstance(result, str)

    def test_misspelled_word_corrected(self):
        result = normalize("I hav a complait", self.sym_spell)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()
