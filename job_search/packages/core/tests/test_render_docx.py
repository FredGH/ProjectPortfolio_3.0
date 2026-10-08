"""Unit tests for the ATS .docx writer and the text extraction."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document
from tests.render_fixtures import make_tailored_document

from core.render.docx_ats import write_docx
from core.render.model import build_render_doc
from core.render.text import diff_texts, extract_docx_text, render_text
from core.tailoring.schema import TailoredExperience

_FORBIDDEN_XML = ("<w:tbl", "txbxContent", "<w:drawing", "<w:pict")


class TestAtsDocx(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "cv.docx"

    def _write(self, **overrides: object) -> None:
        write_docx(build_render_doc(make_tailored_document(**overrides)), self.path)

    def test_extracted_text_equals_the_text_twin(self) -> None:
        doc = build_render_doc(make_tailored_document())
        write_docx(doc, self.path)
        self.assertEqual(extract_docx_text(self.path), render_text(doc))

    def test_sections_read_in_the_intended_order(self) -> None:
        self._write()
        lines = extract_docx_text(self.path).splitlines()
        positions = [
            lines.index(h)
            for h in ("Summary", "Skills", "Experience", "Education", "Certifications")
        ]
        self.assertEqual(positions, sorted(positions))

    def test_the_exact_title_is_in_the_rendered_text(self) -> None:
        title = "Lead Data Engineer – Données & Co"
        self._write(target_title=title, headline=title)
        self.assertIn(title, extract_docx_text(self.path).splitlines())

    def test_the_title_is_the_second_line_after_the_name(self) -> None:
        self._write()
        lines = extract_docx_text(self.path).splitlines()
        self.assertEqual(lines[:2], ["Zz Fixture", "Lead Data Engineer"])

    def test_no_tables_text_boxes_images_headers_or_footers(self) -> None:
        self._write()
        with zipfile.ZipFile(self.path) as package:
            names = package.namelist()
            body = package.read("word/document.xml").decode()
        for marker in _FORBIDDEN_XML:
            self.assertNotIn(marker, body)
        self.assertFalse([n for n in names if "header" in n or "footer" in n])
        self.assertFalse([n for n in names if n.startswith("word/media/")])

    def test_headings_and_bullets_use_real_styles(self) -> None:
        doc = build_render_doc(make_tailored_document())
        write_docx(doc, self.path)
        paragraphs = Document(str(self.path)).paragraphs
        headings = [p.text for p in paragraphs if p.style.name == "Heading 1"]
        bullets = [p.text for p in paragraphs if p.style.name == "List Bullet"]
        self.assertEqual(headings, doc.headings())
        self.assertEqual(bullets, [b.text for b in doc.blocks if b.kind == "bullet"])

    def test_a_missing_company_and_dates_still_produce_a_valid_docx(self) -> None:
        role = TailoredExperience(truth_index=0, company="", title="Engineer")
        self._write(experience=[role])
        self.assertIn("Engineer", extract_docx_text(self.path).splitlines())


class TestDiffTexts(unittest.TestCase):
    def test_equal_texts_have_no_diff(self) -> None:
        self.assertEqual(diff_texts("a\nb\n", "a\nb\n"), [])

    def test_a_reordered_section_is_reported(self) -> None:
        diff = diff_texts("Skills\nExperience\n", "Experience\nSkills\n")
        self.assertTrue(diff)


if __name__ == "__main__":
    unittest.main()
