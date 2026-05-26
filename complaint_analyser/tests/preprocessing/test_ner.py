from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from agentic_triage.preprocessing.ner import extract_entities


def _make_nlp(*ents: tuple[str, str]) -> MagicMock:
    """Build a mock spaCy nlp object returning the given (text, label) entities."""
    mock_nlp = MagicMock()
    mock_doc = MagicMock()
    mock_doc.ents = [
        _make_ent(text, label) for text, label in ents
    ]
    mock_nlp.return_value = mock_doc
    return mock_nlp


def _make_ent(text: str, label: str) -> MagicMock:
    ent = MagicMock()
    ent.text = text
    ent.label_ = label
    return ent


class TestExtractEntities(unittest.TestCase):
    def test_extracts_money_entity(self):
        nlp = _make_nlp(("£500", "MONEY"))
        result = extract_entities("I was charged £500", ["MONEY", "ORG"], nlp)
        self.assertIn("MONEY", result)
        self.assertIn("£500", result["MONEY"])

    def test_extracts_multiple_labels(self):
        nlp = _make_nlp(("£500", "MONEY"), ("HSBC", "ORG"))
        result = extract_entities("HSBC charged me £500", ["MONEY", "ORG"], nlp)
        self.assertIn("MONEY", result)
        self.assertIn("ORG", result)

    def test_filters_labels_not_in_requested(self):
        nlp = _make_nlp(("HSBC", "ORG"), ("London", "GPE"))
        result = extract_entities("HSBC in London", ["ORG"], nlp)
        self.assertIn("ORG", result)
        self.assertNotIn("GPE", result)

    def test_returns_empty_on_no_match(self):
        nlp = _make_nlp()
        result = extract_entities("simple complaint", ["MONEY"], nlp)
        self.assertEqual(result, {})

    def test_multiple_entities_same_label(self):
        nlp = _make_nlp(("£100", "MONEY"), ("£200", "MONEY"))
        result = extract_entities("charged £100 and £200", ["MONEY"], nlp)
        self.assertEqual(len(result["MONEY"]), 2)

    def test_gliner_model_called_when_provided(self):
        nlp = _make_nlp()
        gliner = MagicMock()
        gliner.predict_entities.return_value = [{"label": "CUSTOM", "text": "foo"}]
        result = extract_entities("some text", ["CUSTOM"], nlp, gliner_model=gliner)
        gliner.predict_entities.assert_called_once_with("some text", ["CUSTOM"])
        self.assertIn("CUSTOM", result)
        self.assertIn("foo", result["CUSTOM"])

    def test_gliner_model_not_called_when_none(self):
        nlp = _make_nlp()
        result = extract_entities("some text", ["MONEY"], nlp, gliner_model=None)
        self.assertEqual(result, {})

    def test_gliner_filters_out_of_scope_labels(self):
        nlp = _make_nlp()
        gliner = MagicMock()
        gliner.predict_entities.return_value = [{"label": "NOT_IN_LIST", "text": "x"}]
        result = extract_entities("some text", ["MONEY"], nlp, gliner_model=gliner)
        self.assertNotIn("NOT_IN_LIST", result)


if __name__ == "__main__":
    unittest.main()
