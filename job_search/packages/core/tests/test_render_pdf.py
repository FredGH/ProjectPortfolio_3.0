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
from core.render.model import Block, RenderDoc, build_render_doc
from core.render.pdf_designed import (
    HELVETICA,
    Fonts,
    _flowables,
    _markup,
    _styles,
    find_fonts,
    undrawable_chars,
    write_pdf,
)
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

    def test_an_unreadable_font_directory_falls_back_instead_of_crashing(
        self,
    ) -> None:
        directory = self.root / "locked"
        directory.mkdir()
        directory.chmod(0)
        self.addCleanup(directory.chmod, 0o755)
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

    def test_a_role_that_fits_on_a_page_is_never_split_across_two(self) -> None:
        roles = [
            TailoredExperience(
                truth_index=n,
                company=f"Company{n:02d}",
                title=f"Title{n:02d}",
                bullets=[
                    TailoredBullet(
                        text=f"Role {n:02d} bullet {b} " + "filler words " * 12,
                        origin="original",
                    )
                    for b in range(3 + n % 5)
                ],
            )
            for n in range(14)
        ]
        self._write(experience=roles)
        pages = [page.extract_text() for page in PdfReader(str(self.path)).pages]
        self.assertGreater(len(pages), 2)
        for n in range(14):
            holders = {
                i
                for i, page in enumerate(pages)
                for marker in [f"Title{n:02d}", *(f"Role {n:02d} bullet {b}" for b in range(3 + n % 5))]
                if marker in page
            }
            self.assertEqual(len(holders), 1, f"role {n} spans pages {holders}")

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

    def test_undrawable_characters_are_listed_for_the_helvetica_fallback(
        self,
    ) -> None:
        doc = build_render_doc(make_tailored_document(identity="Zoë Łukasz 日本"))
        self.assertEqual(undrawable_chars(doc, HELVETICA), ["Ł", "日", "本"])

    def test_plain_latin_text_has_no_undrawable_characters(self) -> None:
        doc = build_render_doc(make_tailored_document())
        self.assertEqual(undrawable_chars(doc, HELVETICA), [])

    def test_undrawable_characters_are_checked_against_a_ttf_font_too(self) -> None:
        directory = Path(self._tmp.name) / "fonts"
        directory.mkdir()
        for target, source in (
            ("Calibri.ttf", "Vera.ttf"),
            ("Calibrib.ttf", "VeraBd.ttf"),
            ("Calibrii.ttf", "VeraIt.ttf"),
        ):
            shutil.copy(_VERA_DIR / source, directory / target)
        fonts = find_fonts([directory])
        doc = build_render_doc(make_tailored_document(identity="Zz 日本"))
        self.assertEqual(undrawable_chars(doc, fonts), ["日", "本"])

    def test_no_text_is_drawn_in_helvetica_when_calibri_like_fonts_are_used(
        self,
    ) -> None:
        directory = Path(self._tmp.name) / "fonts"
        directory.mkdir()
        for target, source in (
            ("Calibri.ttf", "Vera.ttf"),
            ("Calibrib.ttf", "VeraBd.ttf"),
            ("Calibrii.ttf", "VeraIt.ttf"),
        ):
            shutil.copy(_VERA_DIR / source, directory / target)
        write_pdf(
            build_render_doc(make_tailored_document()),
            self.path,
            find_fonts([directory]),
        )
        drawn = set()
        for page in PdfReader(str(self.path)).pages:
            names = {
                key: str(font.get_object().get("/BaseFont"))
                for key, font in page["/Resources"]["/Font"].items()
            }
            current = ""
            content = page.get_contents().get_data().decode("latin-1")
            for match in re.finditer(r"(/\S+) [\d.]+ Tf|\) Tj|\] TJ", content):
                if match.group(1):
                    current = names[match.group(1)]
                else:
                    drawn.add(current)
        self.assertTrue(drawn)
        self.assertFalse([name for name in drawn if "Helvetica" in name], drawn)

    def test_role_dates_are_coloured_grey_and_the_rest_is_not(self) -> None:
        block = Block(
            "role_meta", "Acme Bank, 01/2019 – Present", muted="01/2019 – Present"
        )
        markup = _markup(block)
        self.assertTrue(markup.startswith("Acme Bank, "))
        self.assertIn('<font color="#7F7F7F">01/2019 – Present</font>', markup)

    def test_muted_text_that_is_not_at_the_end_is_ignored(self) -> None:
        block = Block("role_meta", "Acme Bank", muted="01/2019")
        self.assertEqual(_markup(block), "Acme Bank")

    def test_bullets_are_indented_like_the_template(self) -> None:
        style = _styles(HELVETICA)["bullet"]
        self.assertEqual((style.bulletIndent, style.leftIndent), (6, 19))

    def test_a_role_with_no_title_gets_the_gap_a_title_would_give(self) -> None:
        styles = _styles(HELVETICA)
        blocks = [
            Block("bullet", "Last bullet of the previous role"),
            Block("role_meta", "Prior experience available on request, 06/1988"),
            Block("heading", "PERSONAL PROJECTS"),
            Block("paragraph", "Something"),
        ]
        flowables = _flowables(blocks, styles)
        self.assertEqual(flowables[1].height, styles["role_title"].spaceBefore)

    def test_the_gap_survives_layout_when_a_heading_follows(self) -> None:
        def pitch(kind: str, text: str) -> float:
            doc = RenderDoc(
                "T",
                (
                    Block("bullet", "B1"),
                    Block(kind, text),
                    Block("heading", "NEXT SECTION"),
                    Block("paragraph", "Body"),
                ),
            )
            write_pdf(doc, self.path, HELVETICA)
            ys: dict[str, float] = {}

            def visit(text: str, cm: list, tm: list, *_: object) -> None:
                ys[text.strip()] = tm[5] * cm[3] + cm[5]

            PdfReader(str(self.path)).pages[0].extract_text(visitor_text=visit)
            return ys["B1"] - ys[text]

        with_gap = pitch("role_meta", "Prior experience on request")
        # A normal bullet-to-bullet step, for comparison.
        write_pdf(
            RenderDoc("T", (Block("bullet", "B1"), Block("bullet", "B2"))),
            self.path,
            HELVETICA,
        )
        ys: dict[str, float] = {}

        def visit(text: str, cm: list, tm: list, *_: object) -> None:
            ys[text.strip()] = tm[5] * cm[3] + cm[5]

        PdfReader(str(self.path)).pages[0].extract_text(visitor_text=visit)
        normal = ys["B1"] - ys["B2"]
        self.assertGreater(with_gap, normal + 5)

    def test_a_title_less_role_is_not_swallowed_by_the_previous_entry(self) -> None:
        styles = _styles(HELVETICA)
        blocks = [
            Block("role_title", "Engineer"),
            Block("role_meta", "Acme, 2020"),
            Block("bullet", "Did things"),
            Block("role_meta", "Prior experience available on request"),
        ]
        flowables = _flowables(blocks, styles)
        self.assertEqual(len(flowables[0]._content), 3)
        self.assertEqual(len(flowables), 3)

    def test_dates_under_a_title_keep_their_tight_spacing(self) -> None:
        styles = _styles(HELVETICA)
        blocks = [Block("role_title", "Engineer"), Block("role_meta", "Acme, 2020")]
        (group,) = _flowables(blocks, styles)
        self.assertEqual(group._content[1].style.spaceBefore, 0)

    def test_highlighted_skills_are_bold_and_the_rest_is_not(self) -> None:
        block = Block("paragraph", "dbt, Airflow, SQL", bold_spans=((0, 3), (14, 17)))
        self.assertEqual(_markup(block), "<b>dbt</b>, Airflow, <b>SQL</b>")

    def test_a_projects_link_is_coloured_blue(self) -> None:
        block = Block(
            "project_title", "AI Projects: example.com/x", link="example.com/x"
        )
        self.assertEqual(
            _markup(block),
            'AI Projects: <font color="#315B94">example.com/x</font>',
        )


if __name__ == "__main__":
    unittest.main()
