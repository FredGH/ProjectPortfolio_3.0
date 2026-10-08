"""Unit tests for the designed PDF writer and its font discovery."""

from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path

import reportlab
from pypdf import PdfReader
from tests.render_fixtures import make_tailored_document

from core.cv.schema import Certification, Project
from core.render.model import build_render_doc
from core.render.pdf_designed import HELVETICA, Fonts, find_fonts, write_pdf
from core.render.text import extract_pdf_text
from core.tailoring.schema import TailoredBullet, TailoredExperience

_VERA_DIR = Path(reportlab.__file__).parent / "fonts"


def _squash(text: str) -> str:
    """Remove all whitespace so extraction quirks cannot matter.

    Args:
        text: Any text.

    Returns:
        The text with every whitespace character removed.
    """
    return re.sub(r"\s+", "", text)


class TestFindFonts(unittest.TestCase):
    def setUp(self) -> None:
        """Create a scratch directory removed after each test."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _calibri_dir(self, name: str) -> Path:
        """Make a directory holding Calibri-named copies of the free Vera fonts.

        Args:
            name: Subdirectory name.

        Returns:
            The directory path.
        """
        directory = self.root / name
        directory.mkdir()
        for target, source in (
            ("Calibri.ttf", "Vera.ttf"),
            ("Calibrib.ttf", "VeraBd.ttf"),
            ("Calibrii.ttf", "VeraIt.ttf"),
        ):
            shutil.copy(_VERA_DIR / source, directory / target)
        return directory

    def test_falls_back_to_helvetica_with_a_warning_when_no_font_is_found(
        self,
    ) -> None:
        with self.assertLogs("core.render.pdf_designed", level="WARNING"):
            fonts = find_fonts([self.root / "missing"])
        self.assertEqual(fonts, HELVETICA)

    def test_uses_calibri_named_fonts_from_a_search_directory(self) -> None:
        fonts = find_fonts([self._calibri_dir("a")])
        self.assertNotEqual(fonts, HELVETICA)
        self.assertEqual(len({fonts.regular, fonts.bold, fonts.italic}), 3)

    def test_the_first_directory_with_the_fonts_wins(self) -> None:
        first = self._calibri_dir("first")
        second = self._calibri_dir("second")
        self.assertEqual(find_fonts([first, second]), find_fonts([first]))
        self.assertNotEqual(find_fonts([first]), find_fonts([second]))

    def test_a_corrupt_font_file_falls_back_instead_of_crashing(self) -> None:
        directory = self.root / "bad"
        directory.mkdir()
        for name in ("Calibri.ttf", "Calibrib.ttf", "Calibrii.ttf"):
            (directory / name).write_bytes(b"not a font")
        with self.assertLogs("core.render.pdf_designed", level="WARNING"):
            fonts = find_fonts([directory])
        self.assertEqual(fonts, HELVETICA)


class TestWritePdf(unittest.TestCase):
    def setUp(self) -> None:
        """Create a scratch output path removed after each test."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "cv.pdf"

    def _write(self, **overrides: object) -> str:
        """Render a fixture CV with Helvetica and return its squashed text.

        Args:
            **overrides: Fields to replace on the fixture document.

        Returns:
            The extracted PDF text with all whitespace removed.
        """
        write_pdf(
            build_render_doc(make_tailored_document(**overrides)),
            self.path,
            HELVETICA,
        )
        return _squash(extract_pdf_text(self.path))

    def test_the_page_is_a4(self) -> None:
        self._write()
        page = PdfReader(str(self.path)).pages[0]
        self.assertAlmostEqual(float(page.mediabox.width), 595.28, delta=0.5)
        self.assertAlmostEqual(float(page.mediabox.height), 841.89, delta=0.5)

    def test_the_title_and_every_heading_appear_in_order(self) -> None:
        doc = build_render_doc(make_tailored_document())
        write_pdf(doc, self.path, HELVETICA)
        text = _squash(extract_pdf_text(self.path))
        self.assertIn(_squash(doc.title), text)
        position = 0
        for heading in doc.headings():
            found = text.find(_squash(heading), position)
            self.assertGreaterEqual(found, 0, heading)
            position = found + len(_squash(heading))

    def test_markup_looking_text_prints_literally(self) -> None:
        role = TailoredExperience(
            truth_index=0,
            company="R&D <Lab>",
            title="Engineer",
            bullets=[
                TailoredBullet(
                    text="Cut <b>cost</b> by 5 < 6 & more", origin="original"
                )
            ],
        )
        text = self._write(experience=[role])
        self.assertIn(_squash("Cut <b>cost</b> by 5 < 6 & more"), text)
        self.assertIn(_squash("R&D <Lab>"), text)

    def test_the_long_qualifications_heading_with_an_ampersand_prints(self) -> None:
        text = self._write(qualifications=[Certification(name="Fixture Cert")])
        self.assertIn(
            _squash("PROFESSIONAL QUALIFICATIONS & CONTINUOUS PERSONAL DEVELOPMENT"),
            text,
        )

    def test_non_latin_text_with_the_fallback_font_does_not_crash(self) -> None:
        project = Project(name="データ基盤", description="日本語の説明")
        self._write(projects=[project])
        self.assertTrue(self.path.exists())

    def test_a_very_long_unbroken_token_and_a_very_long_bullet_do_not_crash(
        self,
    ) -> None:
        url = "https://example.com/" + "a" * 400
        role = TailoredExperience(
            truth_index=0,
            company="Acme",
            title="Engineer",
            bullets=[
                TailoredBullet(text=url, origin="original"),
                TailoredBullet(text="word " * 600, origin="original"),
            ],
        )
        text = self._write(experience=[role])
        self.assertIn(_squash(url), text)

    def test_a_long_cv_flows_onto_more_pages_without_losing_text(self) -> None:
        bullets = [
            TailoredBullet(
                text=f"Delivered numbered item {n:03d} here", origin="original"
            )
            for n in range(120)
        ]
        role = TailoredExperience(
            truth_index=0, company="Acme", title="Engineer", bullets=bullets
        )
        text = self._write(experience=[role])
        self.assertGreater(len(PdfReader(str(self.path)).pages), 1)
        self.assertIn("numbereditem000here", text)
        self.assertIn("numbereditem119here", text)

    def test_the_linkedin_url_is_still_in_the_text(self) -> None:
        self.assertIn("linkedin.com/in/zzfixture", self._write())

    def test_writing_with_registered_vera_fonts_works(self) -> None:
        directory = Path(self._tmp.name) / "fonts"
        directory.mkdir()
        for target, source in (
            ("Calibri.ttf", "Vera.ttf"),
            ("Calibrib.ttf", "VeraBd.ttf"),
            ("Calibrii.ttf", "VeraIt.ttf"),
        ):
            shutil.copy(_VERA_DIR / source, directory / target)
        fonts = find_fonts([directory])
        self.assertIsInstance(fonts, Fonts)
        write_pdf(build_render_doc(make_tailored_document()), self.path, fonts)
        self.assertIn("ZzFixture", _squash(extract_pdf_text(self.path)))


if __name__ == "__main__":
    unittest.main()
