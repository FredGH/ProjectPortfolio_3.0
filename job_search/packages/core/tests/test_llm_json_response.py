"""Unit tests for core.llm.json_response."""

from __future__ import annotations

import json
import unittest

from core.llm.json_response import parse_json_response


class TestParseJsonResponse(unittest.TestCase):
    def test_parses_a_bare_object(self) -> None:
        self.assertEqual(parse_json_response('{"a": 1}'), {"a": 1})

    def test_parses_a_fenced_block(self) -> None:
        self.assertEqual(parse_json_response('```json\n{"a": 1}\n```'), {"a": 1})

    def test_parses_a_fence_preceded_by_prose(self) -> None:
        text = 'Sure, here you go:\n```json\n{"a": 1}\n```'
        self.assertEqual(parse_json_response(text), {"a": 1})

    def test_parses_an_object_embedded_in_prose(self) -> None:
        self.assertEqual(
            parse_json_response('Here is the result {"a": 1} hope it helps'), {"a": 1}
        )

    def test_raises_when_no_candidate_parses(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            parse_json_response("no json here")


if __name__ == "__main__":
    unittest.main()
