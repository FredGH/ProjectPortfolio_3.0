"""Unit tests for the CV render service."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.render_fixtures import make_tailored_document

from core.render.service import RenderError, render_cv_files
from core.render.text import extract_docx_text


class TestRenderCvFiles(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "nested" / "out"

    def test_writes_a_docx_and_matching_txt_with_the_convention_name(self) -> None:
        files = render_cv_files(make_tailored_document(), "Acme Bank", self.out)
        self.assertEqual(
            files.docx_path.name, "Fixture_Lead_Data_Engineer_Acme_Bank.docx"
        )
        self.assertEqual(files.txt_path.suffix, ".txt")
        self.assertEqual(
            files.txt_path.read_text(encoding="utf-8"),
            extract_docx_text(files.docx_path),
        )

    def test_a_missing_company_is_left_out_of_the_filename(self) -> None:
        files = render_cv_files(make_tailored_document(), None, self.out)
        self.assertEqual(files.docx_path.name, "Fixture_Lead_Data_Engineer.docx")

    def test_a_parse_order_mismatch_fails_and_leaves_no_files(self) -> None:
        with mock.patch(
            "core.render.service.extract_docx_text", return_value="wrong\n"
        ):
            with self.assertRaises(RenderError):
                render_cv_files(make_tailored_document(), "Acme", self.out)
        self.assertEqual(list(self.out.glob("*")), [])

    def test_a_blank_title_is_refused_before_anything_is_written(self) -> None:
        with self.assertRaises(ValueError):
            render_cv_files(make_tailored_document(target_title=" "), "Acme", self.out)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
