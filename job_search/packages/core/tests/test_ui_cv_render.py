"""Unit tests for turning an approved tailored CV into downloadable files."""

from __future__ import annotations

import unittest

from tests.render_fixtures import make_tailored_document

from core.ui.cv_render import render_for_download


class TestRenderForDownload(unittest.TestCase):
    def test_returns_a_pdf_a_docx_and_a_txt_in_that_order(self) -> None:
        bundle = render_for_download(make_tailored_document().model_dump(), "Acme Bank")
        self.assertEqual(
            [f.name for f in bundle.files],
            [
                "Fixture_Lead_Data_Engineer_Acme_Bank.pdf",
                "Fixture_Lead_Data_Engineer_Acme_Bank.docx",
                "Fixture_Lead_Data_Engineer_Acme_Bank.txt",
            ],
        )

    def test_the_files_have_the_right_content_types_and_signatures(self) -> None:
        bundle = render_for_download(make_tailored_document().model_dump(), None)
        pdf, docx, txt = bundle.files
        self.assertEqual(pdf.mime, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertTrue(docx.data.startswith(b"PK"))
        self.assertIn("wordprocessingml", docx.mime)
        self.assertEqual(txt.mime, "text/plain")
        self.assertIn(b"Zz Fixture", txt.data)

    def test_a_missing_company_is_left_out_of_the_names(self) -> None:
        bundle = render_for_download(make_tailored_document().model_dump(), None)
        self.assertEqual(bundle.files[0].name, "Fixture_Lead_Data_Engineer.pdf")

    def test_warnings_are_passed_through(self) -> None:
        document = make_tailored_document().model_dump()
        document["experience"][0]["bullets"][0]["text"] = "Built 日本語 dashboards"
        bundle = render_for_download(document, "Acme")
        self.assertTrue(any("日" in w for w in bundle.warnings))

    def test_a_blank_title_raises_a_value_error(self) -> None:
        document = make_tailored_document(target_title=" ").model_dump()
        with self.assertRaises(ValueError):
            render_for_download(document, "Acme")


if __name__ == "__main__":
    unittest.main()
