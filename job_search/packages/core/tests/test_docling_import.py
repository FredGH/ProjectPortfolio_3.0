"""Smoke test: docling must be importable and expose DocumentConverter."""

from __future__ import annotations

import unittest


class TestDoclingImport(unittest.TestCase):
    def test_document_converter_is_importable(self) -> None:
        from docling.document_converter import DocumentConverter

        self.assertTrue(callable(DocumentConverter))


if __name__ == "__main__":
    unittest.main()
