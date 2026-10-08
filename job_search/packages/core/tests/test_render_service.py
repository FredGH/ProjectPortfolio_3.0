"""Unit tests for the CV render service."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.render_fixtures import make_tailored_document

from core.render.pdf_designed import HELVETICA
from core.render.service import RenderError, render_cv_files
from core.render.text import extract_docx_text, extract_pdf_text


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

    def test_a_title_with_extra_whitespace_still_renders(self) -> None:
        title = "Lead  Data Engineer"
        files = render_cv_files(
            make_tailored_document(target_title=title, headline=title), "Acme", self.out
        )
        self.assertIn(
            "Lead Data Engineer", extract_docx_text(files.docx_path).splitlines()
        )

    def test_a_control_character_in_the_text_still_renders(self) -> None:
        document = make_tailored_document()
        document.experience[0].bullets[0].text = "Built\x00 dbt\x01 models"
        files = render_cv_files(document, "Acme", self.out)
        self.assertIn(
            "Built dbt models", extract_docx_text(files.docx_path).splitlines()
        )

    def test_a_very_long_title_still_renders_with_a_safe_filename(self) -> None:
        title = "Lead Data Engineer " * 30
        files = render_cv_files(
            make_tailored_document(target_title=title, headline=title), "Acme", self.out
        )
        self.assertLessEqual(len(files.docx_path.name.encode()), 255)
        self.assertTrue(files.docx_path.exists())

    def test_writes_a_pdf_beside_the_docx_by_default(self) -> None:
        files = render_cv_files(
            make_tailored_document(), "Acme Bank", self.out, fonts=HELVETICA
        )
        self.assertEqual(
            files.pdf_path.name, "Fixture_Lead_Data_Engineer_Acme_Bank.pdf"
        )
        self.assertIn(
            "LeadDataEngineer", "".join(extract_pdf_text(files.pdf_path).split())
        )

    def test_ats_only_writes_no_pdf(self) -> None:
        files = render_cv_files(
            make_tailored_document(), "Acme", self.out, with_pdf=False
        )
        self.assertIsNone(files.pdf_path)
        self.assertEqual(
            [p.suffix for p in sorted(self.out.iterdir())], [".docx", ".txt"]
        )

    def test_a_pdf_missing_the_title_fails_and_removes_every_file(self) -> None:
        with mock.patch(
            "core.render.service.extract_pdf_text", return_value="nothing useful"
        ):
            with self.assertRaises(RenderError):
                render_cv_files(
                    make_tailored_document(), "Acme", self.out, fonts=HELVETICA
                )
        self.assertEqual(list(self.out.glob("*")), [])

    def test_a_pdf_with_headings_out_of_order_fails(self) -> None:
        text = "Lead Data Engineer\nEDUCATION\nPROFESSIONAL SUMMARY\n"
        with mock.patch("core.render.service.extract_pdf_text", return_value=text):
            with self.assertRaises(RenderError):
                render_cv_files(
                    make_tailored_document(), "Acme", self.out, fonts=HELVETICA
                )

    def test_pdf_verification_ignores_whitespace_and_line_wrapping(self) -> None:
        files = render_cv_files(
            make_tailored_document(), "Acme", self.out, fonts=HELVETICA
        )
        self.assertTrue(files.pdf_path.exists())


if __name__ == "__main__":
    unittest.main()
