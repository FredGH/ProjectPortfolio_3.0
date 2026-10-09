# Step 18b — Designed PDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `render-cv` also write a designed PDF that looks like the user's own CV template, from the same `RenderDoc` as the ATS `.docx`.

**Architecture:** `core/render/pdf_designed.py` maps each `Block` kind to a reportlab paragraph style (A4, Calibri-like, blue name and headings) and builds the PDF. Fonts are found at run time (git-ignored `private/fonts/`, then the Calibri copies shipped with Microsoft Word on macOS, then Helvetica with a warning) because Calibri is proprietary and the repo is public. `render_cv_files` verifies the PDF by extracting its text (title present, headings in order) and removes every written file on failure.

**Tech Stack:** Python 3.11, reportlab 5.0.1, pypdf 6.19.0, `unittest`. Builds on 18a (PR #54): `core.render.model.RenderDoc`, `core.render.service.render_cv_files`, `render-cv`.

**Spec:** [docs/superpowers/specs/2026-10-08-step18b-designed-pdf-design.md](../specs/2026-10-08-step18b-designed-pdf-design.md) (and the shared [Step 18 spec](../specs/2026-10-08-step18-cv-rendering-design.md)).

## Global Constraints

- The repo is **public**: no CV, no proprietary font and no output is committed. Tests use invented fixtures (`Zz Fixture`) and either Helvetica or the free Bitstream Vera fonts that ship inside `reportlab`.
- The PDF is **flat**: no bold key phrases; wording, order, headings, dates and acronym expansion come from `RenderDoc` unchanged.
- A4, one column, ~50 pt side margins, ~40 pt top and bottom margins; Calibri (regular/bold/italic) when found; name and section headings blue `#2F5597`.
- Text handed to reportlab is XML-escaped (`&`, `<`, `>`): a heading contains `&`.
- A missing, unreadable or corrupt font never crashes a render: fall back to Helvetica with a logged warning.
- `render-cv` writes the PDF by default; `--ats-only` skips it.
- Code rules (`.claude/rules/python-style.md`): Python 3.11, black/isort/ruff (88 cols), `from __future__ import annotations`, type hints, Google-style docstrings with `Args`/`Returns`/`Raises` on every function and class (nested and private too, and test helpers).
- Tests (`.claude/rules/python-testing.md`): `unittest`, one file per module under `packages/core/tests/`, behaviour-named methods.
- Run tests from `packages/core` under arm64 with the project `.env` loaded (needed by the CLI tests): `cd packages/core && bash -c 'set -a; . ../../.env; set +a; arch -arm64 ../../venv/bin/python -m unittest tests.<module> -v'`.
- Format only the files you touched, from `packages/core`: `../../../.superpowers/fmt.sh <paths>` (black, isort, ruff). Never `isort .`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. `&`, `<`, `>` and markup-looking text (`<b>x</b>`) in any block: must print literally, not be parsed (Task 1).
2. A corrupt or truncated font file named `Calibri.ttf` in `private/fonts/`: fall back, never crash (Task 1).
3. Non-Latin text with the Helvetica fallback: no crash (Task 1).
4. A very long unbroken token (a 400-character URL) and a 3000-character bullet: wraps or splits, never raises a layout error (Task 1).
5. A CV long enough for several pages: nothing cut, headings never stranded at a page end (Task 1).
6. PDF verification must not false-fail on extraction quirks (wrapped lines, spacing): compare with whitespace removed (Task 2).

---

### Task 1: The PDF writer, font discovery and PDF text extraction

**Files:**
- Create: `packages/core/core/render/pdf_designed.py`
- Modify: `packages/core/core/render/text.py` (add `extract_pdf_text`)
- Modify: `requirements.txt` (add `reportlab==5.0.1`, `pypdf==6.19.0`)
- Test: `packages/core/tests/test_render_pdf.py`

**Interfaces:**
- Consumes: `core.render.model.RenderDoc`, `Block`, `BlockKind`, `build_render_doc`; `tests.render_fixtures.make_tailored_document`.
- Produces:
  - `@dataclass(frozen=True) class Fonts: regular: str; bold: str; italic: str`
  - `HELVETICA: Fonts`
  - `find_fonts(search_dirs: Sequence[Path] | None = None) -> Fonts`
  - `write_pdf(doc: RenderDoc, path: Path, fonts: Fonts | None = None) -> None`
  - `extract_pdf_text(path: Path) -> str`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_render_pdf.py`:

```python
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
                TailoredBullet(text="Cut <b>cost</b> by 5 < 6 & more", origin="original")
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
            TailoredBullet(text=f"Delivered numbered item {n:03d} here", origin="original")
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/core && bash -c 'set -a; . ../../.env; set +a; arch -arm64 ../../venv/bin/python -m unittest tests.test_render_pdf -v'`
Expected: ERROR `ModuleNotFoundError: No module named 'core.render.pdf_designed'`

- [ ] **Step 3: Implement**

Add to `packages/core/core/render/text.py` (extend imports with `from pypdf import PdfReader`):

```python
def extract_pdf_text(path: Path) -> str:
    """Read a PDF's text, page by page.

    Args:
        path: The PDF file.

    Returns:
        The text of every page, joined by newlines.
    """
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
```

Create `packages/core/core/render/pdf_designed.py`:

```python
"""The designed PDF writer (Step 18b): the same RenderDoc as the ATS .docx,
laid out like the user's own CV template (A4, one column, Calibri, blue name
and section headings).

Calibri is proprietary and the repo is public, so fonts are found at run
time rather than committed (see `find_fonts`).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, KeepTogether, Paragraph, SimpleDocTemplate

from core.render.model import Block, BlockKind, RenderDoc

logger = logging.getLogger(__name__)

_BLUE = HexColor("#2F5597")
_BLUE_HEX = "#2F5597"
_MARGIN_X = 50
_MARGIN_Y = 40

_JOB_SEARCH_DIR = Path(__file__).resolve().parents[4]
PRIVATE_FONT_DIR = _JOB_SEARCH_DIR / "private" / "fonts"
"""Git-ignored folder where the user may drop their own Calibri files."""
WORD_FONT_DIR = Path("/Applications/Microsoft Word.app/Contents/Resources/DFonts")
"""Where Microsoft Word for macOS keeps its Calibri files."""
DEFAULT_FONT_DIRS = (PRIVATE_FONT_DIR, WORD_FONT_DIR)
_FONT_FILES = ("calibri.ttf", "calibrib.ttf", "calibrii.ttf")


@dataclass(frozen=True)
class Fonts:
    """The three faces the PDF uses.

    Attributes:
        regular: reportlab font name for body text.
        bold: reportlab font name for bold text.
        italic: reportlab font name for italic text.
    """

    regular: str
    bold: str
    italic: str


HELVETICA = Fonts("Helvetica", "Helvetica-Bold", "Helvetica-Oblique")
"""reportlab's built-in fallback; needs no files."""


def _register(path: Path) -> str:
    """Register one TrueType file with reportlab, once.

    Args:
        path: The `.ttf` file.

    Returns:
        The name the font is registered under (unique per file path, so
        fonts from different folders never clash).
    """
    digest = hashlib.md5(str(path).encode()).hexdigest()[:8]
    name = f"cv-{path.stem.lower()}-{digest}"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(path)))
    return name


def find_fonts(search_dirs: Sequence[Path] | None = None) -> Fonts:
    """Find Calibri (regular, bold, italic) to match the user's template.

    Args:
        search_dirs: Folders to look in, in order; defaults to
            `private/fonts/` then the copies shipped with Microsoft Word.

    Returns:
        The first complete Calibri set that registers cleanly; otherwise
        Helvetica, with a logged warning that the PDF will not match the
        template's typeface.
    """
    for directory in DEFAULT_FONT_DIRS if search_dirs is None else search_dirs:
        if not directory.is_dir():
            continue
        found = {entry.name.lower(): entry for entry in directory.iterdir()}
        if not all(name in found for name in _FONT_FILES):
            continue
        try:
            regular, bold, italic = (_register(found[n]) for n in _FONT_FILES)
        except Exception as exc:  # reportlab raises assorted errors on bad fonts
            logger.warning("ignoring unreadable fonts in %s: %s", directory, exc)
            continue
        return Fonts(regular, bold, italic)
    logger.warning(
        "Calibri not found; the PDF uses Helvetica and will not match the "
        "template's typeface (put Calibri*.ttf in private/fonts/)"
    )
    return HELVETICA


def _styles(fonts: Fonts) -> dict[BlockKind, ParagraphStyle]:
    """Map each block kind to its paragraph style.

    Args:
        fonts: The font names to use.

    Returns:
        One style per block kind, tuned against the user's template.
    """

    def style(name: str, font: str, size: float, **kwargs: object) -> ParagraphStyle:
        """Build one paragraph style.

        Args:
            name: Style name.
            font: reportlab font name.
            size: Font size in points.
            **kwargs: Extra `ParagraphStyle` fields.

        Returns:
            The style, with leading derived from the size.
        """
        return ParagraphStyle(
            name,
            fontName=font,
            fontSize=size,
            leading=kwargs.pop("leading", size * 1.25),
            textColor=kwargs.pop("textColor", black),
            spaceAfter=kwargs.pop("spaceAfter", 2),
            **kwargs,
        )

    return {
        "name": style("name", fonts.bold, 20, textColor=_BLUE, spaceAfter=4),
        "headline": style("headline", fonts.bold, 11, spaceAfter=3),
        "contact": style("contact", fonts.regular, 9.5, spaceAfter=6),
        "heading": style(
            "heading",
            fonts.bold,
            10.5,
            textColor=_BLUE,
            spaceBefore=8,
            spaceAfter=3,
            keepWithNext=1,
        ),
        "role_title": style(
            "role_title", fonts.bold, 10, spaceBefore=5, spaceAfter=0, keepWithNext=1
        ),
        "role_meta": style(
            "role_meta", fonts.italic, 9.5, spaceAfter=2, keepWithNext=1
        ),
        "paragraph": style("paragraph", fonts.regular, 9.5),
        "bullet": style(
            "bullet", fonts.regular, 9.5, leftIndent=14, bulletIndent=3, spaceAfter=1
        ),
    }


def _markup(block: Block) -> str:
    """Turn a block's text into safe reportlab paragraph markup.

    Args:
        block: The block.

    Returns:
        The text with `&`, `<`, `>` escaped; in the contact line the
        LinkedIn URL is also coloured blue and underlined.
    """
    if block.kind != "contact":
        return escape(block.text)
    parts = [escape(part) for part in block.text.split(" | ")]
    parts = [
        f'<font color="{_BLUE_HEX}"><u>{part}</u></font>'
        if "linkedin.com" in part
        else part
        for part in parts
    ]
    return " | ".join(parts)


def _paragraph(block: Block, styles: dict[BlockKind, ParagraphStyle]) -> Paragraph:
    """Build the paragraph for one block.

    Args:
        block: The block.
        styles: The style table from `_styles`.

    Returns:
        A reportlab paragraph (bullets get a `•` marker).
    """
    if block.kind == "bullet":
        return Paragraph(_markup(block), styles["bullet"], bulletText="•")
    return Paragraph(_markup(block), styles[block.kind])


def _flowables(
    blocks: Sequence[Block], styles: dict[BlockKind, ParagraphStyle]
) -> list[Flowable]:
    """Lay the blocks out, keeping each role heading with its first bullet.

    Args:
        blocks: The document's blocks in reading order.
        styles: The style table from `_styles`.

    Returns:
        The flowables to build, so a page never ends on a lone role title.
    """
    flowables: list[Flowable] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if block.kind != "role_title":
            flowables.append(_paragraph(block, styles))
            index += 1
            continue
        group = [_paragraph(block, styles)]
        index += 1
        for kind in ("role_meta", "bullet"):
            if index < len(blocks) and blocks[index].kind == kind:
                group.append(_paragraph(blocks[index], styles))
                index += 1
        flowables.append(KeepTogether(group))
    return flowables


def write_pdf(doc: RenderDoc, path: Path, fonts: Fonts | None = None) -> None:
    """Write the designed PDF.

    Args:
        doc: The render model (the same one the .docx is written from).
        path: Where to save the file.
        fonts: The fonts to use; found with `find_fonts()` when omitted.
    """
    styles = _styles(fonts or find_fonts())
    template = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=_MARGIN_X,
        rightMargin=_MARGIN_X,
        topMargin=_MARGIN_Y,
        bottomMargin=_MARGIN_Y,
        title=doc.title[:255],
        author="",
    )
    template.build(_flowables(doc.blocks, styles))
```

Append to `requirements.txt` after `python-docx==1.2.0`:

```
reportlab==5.0.1
pypdf==6.19.0
```

- [ ] **Step 4: Run to verify pass**

Run the Step 2 command.
Expected: PASS (13 tests). If `test_a_very_long_unbroken_token…` raises `LayoutError`, set `splitLongWords=1` and `wordWrap=None` in the `style()` helper defaults and re-run. If `test_a_corrupt_font_file…` raises instead of falling back, the exception type escapes the `except Exception`; it must not, so debug the traceback (`registerFont` may have succeeded on garbage: then validate by constructing the `TTFont` before registering).

- [ ] **Step 5: Format, lint, commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/render tests/test_render_pdf.py && cd "$(git rev-parse --show-toplevel)" && git add -A job_search && git commit -m "feat(job_search): designed PDF writer with Calibri discovery and Helvetica fallback

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: PDF in the render service, with verification

**Files:**
- Modify: `packages/core/core/render/service.py`
- Test: `packages/core/tests/test_render_service.py` (append)

**Interfaces:**
- Consumes: Task 1's `write_pdf`, `find_fonts`, `Fonts`, `HELVETICA`, `extract_pdf_text`; 18a's `render_cv_files`, `RenderedCv`, `RenderError`.
- Produces:
  - `RenderedCv` gains `pdf_path: Path | None = None`
  - `render_cv_files(tailored, company, out_dir, *, with_pdf: bool = True, fonts: Fonts | None = None) -> RenderedCv`

- [ ] **Step 1: Write the failing tests**

Append to the class `TestRenderCvFiles` in `packages/core/tests/test_render_service.py` (add `from core.render.pdf_designed import HELVETICA` and `from core.render.text import extract_pdf_text` to the imports):

```python
    def test_writes_a_pdf_beside_the_docx_by_default(self) -> None:
        files = render_cv_files(
            make_tailored_document(), "Acme Bank", self.out, fonts=HELVETICA
        )
        self.assertEqual(
            files.pdf_path.name, "Fixture_Lead_Data_Engineer_Acme_Bank.pdf"
        )
        self.assertIn("LeadDataEngineer", "".join(extract_pdf_text(files.pdf_path).split()))

    def test_ats_only_writes_no_pdf(self) -> None:
        files = render_cv_files(
            make_tailored_document(), "Acme", self.out, with_pdf=False
        )
        self.assertIsNone(files.pdf_path)
        self.assertEqual([p.suffix for p in sorted(self.out.iterdir())], [".docx", ".txt"])

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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/core && bash -c 'set -a; . ../../.env; set +a; arch -arm64 ../../venv/bin/python -m unittest tests.test_render_service -v'`
Expected: ERRORs: `render_cv_files() got an unexpected keyword argument 'fonts'`.

- [ ] **Step 3: Implement**

In `packages/core/core/render/service.py`: add imports `import re`, `from core.render.pdf_designed import Fonts, write_pdf`, and extend the text import to `from core.render.text import diff_texts, extract_docx_text, extract_pdf_text, render_text`; add `from core.render.model import RenderDoc` beside the existing model import. Replace `RenderedCv` and `render_cv_files` with:

```python
@dataclass(frozen=True)
class RenderedCv:
    """The files written for one CV.

    Attributes:
        docx_path: The ATS .docx.
        txt_path: The plain-text twin it was verified against.
        pdf_path: The designed PDF, or None when it was not requested.
    """

    docx_path: Path
    txt_path: Path
    pdf_path: Path | None = None


def _squash(text: str) -> str:
    """Remove all whitespace, so wrapping and spacing cannot matter.

    Args:
        text: Any text.

    Returns:
        The text with every whitespace character removed.
    """
    return re.sub(r"\s+", "", text)


def _pdf_problems(render_doc: RenderDoc, pdf_path: Path) -> list[str]:
    """Check that the PDF reads back with the exact title and headings in order.

    Args:
        render_doc: The model the PDF was written from.
        pdf_path: The written PDF.

    Returns:
        A list of problems; empty when the PDF is as intended.
    """
    try:
        extracted = extract_pdf_text(pdf_path)
    except Exception as exc:  # pypdf raises assorted errors on a bad file
        return [f"the PDF cannot be read back: {exc}"]
    text = _squash(extracted)
    problems: list[str] = []
    if _squash(render_doc.title) not in text:
        problems.append("the exact target title is missing from the PDF text")
    position = 0
    for heading in render_doc.headings():
        found = text.find(_squash(heading), position)
        if found < 0:
            problems.append(f"PDF heading missing or out of order: {heading}")
            break
        position = found + len(_squash(heading))
    return problems


def render_cv_files(
    tailored: TailoredDocument,
    company: str | None,
    out_dir: Path,
    *,
    with_pdf: bool = True,
    fonts: Fonts | None = None,
) -> RenderedCv:
    """Render, write and verify the .docx and .txt (and the PDF) for one CV.

    Args:
        tailored: The approved tailored document.
        company: The target employer for the filename, or None.
        out_dir: Directory to write into (created if missing).
        with_pdf: Also write the designed PDF.
        fonts: Fonts for the PDF; found automatically when omitted.

    Returns:
        The written paths.

    Raises:
        ValueError: If the target title is blank (nothing is written).
        RenderError: If the text extracted from the .docx differs from the
            .txt twin, or the .docx or PDF lacks the exact target title, or
            the PDF headings are missing or out of order; every written
            file is removed.
    """
    render_doc = build_render_doc(tailored)
    out_dir.mkdir(parents=True, exist_ok=True)
    docx_path = out_dir / build_filename(
        tailored.identity, tailored.target_title, company, "docx"
    )
    txt_path = docx_path.with_suffix(".txt")
    pdf_path = docx_path.with_suffix(".pdf") if with_pdf else None
    expected = render_text(render_doc)
    write_docx(render_doc, docx_path)
    txt_path.write_text(expected, encoding="utf-8")
    extracted = extract_docx_text(docx_path)
    problems = diff_texts(expected, extracted)
    if render_doc.title not in extracted.splitlines():
        problems.append("the exact target title is missing from the .docx text")
    if pdf_path is not None:
        write_pdf(render_doc, pdf_path, fonts)
        problems.extend(_pdf_problems(render_doc, pdf_path))
    if problems:
        for written in (docx_path, txt_path, pdf_path):
            if written is not None:
                written.unlink(missing_ok=True)
        raise RenderError(
            "the output does not read back as intended:\n" + "\n".join(problems[:20])
        )
    return RenderedCv(docx_path=docx_path, txt_path=txt_path, pdf_path=pdf_path)
```

Existing 18a tests that assert a mismatch or a message must keep passing: the 18a text in `RenderError` changed from "the .docx does not read back" to "the output does not read back"; `test_a_parse_order_mismatch_fails_and_leaves_no_files` asserts only the exception and an empty folder, so it still passes.

- [ ] **Step 4: Run to verify pass**

Run: `cd packages/core && bash -c 'set -a; . ../../.env; set +a; arch -arm64 ../../venv/bin/python -m unittest tests.test_render_service tests.test_render_pdf tests.test_render_docx -v'`
Expected: PASS. Note the older service tests (`test_writes_a_docx_and_matching_txt_…`, `test_a_missing_company_…`) now also write a PDF with whatever fonts `find_fonts()` finds (Calibri on this Mac, else Helvetica): that is fine, but `test_a_missing_company_is_left_out_of_the_filename` asserts only the docx name.

- [ ] **Step 5: Format, lint, commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/render tests/test_render_service.py && cd "$(git rev-parse --show-toplevel)" && git add -A job_search && git commit -m "feat(job_search): render service writes and verifies the designed PDF

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: CLI flag, README, and visual tuning

**Files:**
- Modify: `apps/pipeline/app/cli.py` (the `render-cv` subparser and `_cmd_render_cv`)
- Modify: `packages/core/tests/test_pipeline_cli_render.py`
- Modify: `README.md` (the "Rendered CV" section)
- Modify (only if tuning needs it): `packages/core/core/render/pdf_designed.py` (sizes, colours, margins)

**Interfaces:**
- Consumes: Task 2's `render_cv_files(..., with_pdf=...)`, `RenderedCv.pdf_path`.
- Produces: CLI `render-cv ... [--ats-only]`, and a completion line `render-cv complete: docx=… txt=… pdf=…`.

- [ ] **Step 1: Update and add the failing CLI tests**

In `packages/core/tests/test_pipeline_cli_render.py` replace `test_an_approved_run_writes_both_files` with these two tests:

```python
    def test_an_approved_run_writes_docx_txt_and_pdf_by_default(self) -> None:
        run = SimpleNamespace(status="approved", document=make_tailored_document())
        with tempfile.TemporaryDirectory() as tmp:
            code, text = _run(["render-cv", *_ARGS, "--out-dir", tmp], run)
            names = sorted(p.name for p in Path(tmp).iterdir())
        self.assertEqual(code, 0)
        base = "Fixture_Lead_Data_Engineer_Acme_Bank"
        self.assertEqual(names, [f"{base}.docx", f"{base}.pdf", f"{base}.txt"])
        self.assertIn("render-cv complete", text)
        self.assertIn(".pdf", text)

    def test_ats_only_skips_the_pdf(self) -> None:
        run = SimpleNamespace(status="approved", document=make_tailored_document())
        with tempfile.TemporaryDirectory() as tmp:
            code, text = _run(
                ["render-cv", *_ARGS, "--out-dir", tmp, "--ats-only"], run
            )
            names = sorted(p.suffix for p in Path(tmp).iterdir())
        self.assertEqual(code, 0)
        self.assertEqual(names, [".docx", ".txt"])
        self.assertNotIn(".pdf", text)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/core && bash -c 'set -a; . ../../.env; set +a; arch -arm64 ../../venv/bin/python -m unittest tests.test_pipeline_cli_render -v'`
Expected: FAIL (`--ats-only` unrecognised; the default test expects a PDF that the CLI does not pass through yet).

- [ ] **Step 3: Implement the CLI change**

In `apps/pipeline/app/cli.py`:

1. After the `--out-dir` argument of `render_cv_parser` add:

```python
    render_cv_parser.add_argument(
        "--ats-only",
        action="store_true",
        help="Write only the ATS .docx and .txt, not the designed PDF",
    )
```

2. In `_cmd_render_cv` change the render call and the success line to:

```python
        files = render_cv_files(
            run.document,
            job.company if job else None,
            Path(args.out_dir),
            with_pdf=not args.ats_only,
        )
```
```python
    print(
        f"render-cv complete: docx={files.docx_path} txt={files.txt_path}"
        + (f" pdf={files.pdf_path}" if files.pdf_path else "")
    )
```
and extend the docstring `Args` to mention `ats_only`.

3. Update the subparser help to `"Write the ATS-safe .docx and .txt and the designed PDF for one approved tailored CV (PLAN.md Step 18); on demand, not a pipeline stage"`.

- [ ] **Step 4: Run to verify pass**

Run: `cd packages/core && bash -c 'set -a; . ../../.env; set +a; arch -arm64 ../../venv/bin/python -m unittest tests.test_pipeline_cli_render tests.test_pipeline_registry tests.test_render_format tests.test_render_model tests.test_render_docx tests.test_render_pdf tests.test_render_service -v'`
Expected: PASS.

- [ ] **Step 5: README**

In `README.md`, section "Rendered CV (Step 18a: ATS .docx)": rename the heading to `## Rendered CV (Step 18: ATS .docx and designed PDF)`; after the existing paragraph ending `The designed PDF is Step 18b.` replace that sentence with:

```markdown
By default it also writes a **designed PDF** (`.pdf`, same name) that looks
like your own CV template: A4, one column, Calibri, blue name and section
headings. Calibri is a Microsoft font and this repo is public, so the font is
never committed: the PDF uses `private/fonts/` (drop `Calibri.ttf`,
`Calibrib.ttf`, `Calibrii.ttf` there; git-ignored), then the copies that ship
with Microsoft Word on macOS, and otherwise falls back to Helvetica with a
warning. Inside Docker only the fallback is available unless you mount your
fonts. The command re-reads the PDF and fails, deleting every file, if the
exact job title is missing or the section headings are missing or out of
order. Pass `--ats-only` to skip the PDF.
```

- [ ] **Step 6: Visual tuning against the template**

Render the fixture CV and compare it with the user's template side by side:

```bash
cd packages/core && arch -arm64 ../../venv/bin/python -c "
from pathlib import Path
from core.cv.schema import Project, Publication, Activity
from tests.render_fixtures import make_tailored_document
from core.render.service import render_cv_files
d = make_tailored_document(
    projects=[Project(name='Fixture Pipeline', description='Open ETL')],
    publications=[Publication(citation='Fixture, Z. (2020). A paper.')],
    activities=[Activity(name='Fixture Club', organisation='Zz')])
print(render_cv_files(d, 'Acme Bank', Path('../../output')).pdf_path)"
cd ../.. && sips -s format png output/Fixture_Lead_Data_Engineer_Acme_Bank.pdf --out /private/tmp/claude-501/fixture_pdf.png && sips -s format png private/FirstName_LastName_2026_v4_template.pdf --out /private/tmp/claude-501/template_pdf.png
```

Open both PNGs (Read tool) and compare name size and colour, heading colour and spacing, role/meta line styling, bullet indent, margins. Adjust the numbers in `_styles` / `_MARGIN_*` / `_BLUE` in `pdf_designed.py` until the first page reads like the template, re-run the Task 1 tests (they assert text and page size, not sizes), and note the final values in the commit message. Skip nothing here: this is the user's agreed tuning step (spec question 2). Report the comparison honestly, including anything that still differs.

- [ ] **Step 7: Format, lint, full affected suite, commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/render tests/test_pipeline_cli_render.py ../../apps/pipeline/app/cli.py && bash -c 'set -a; . ../../.env; set +a; arch -arm64 ../../venv/bin/python -m unittest tests.test_pipeline_cli_render tests.test_pipeline_registry tests.test_pipeline_cli_tailor tests.test_render_format tests.test_render_model tests.test_render_docx tests.test_render_pdf tests.test_render_service 2>&1 | tail -4' && cd "$(git rev-parse --show-toplevel)" && git add -A job_search && git commit -m "feat(job_search): render-cv writes the designed PDF by default (--ats-only to skip)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage:** engine reportlab, pins (Task 1); `write_pdf`, `find_fonts`, style table, A4/margins, KeepTogether, flat text (Task 1); fonts order `private/fonts/` → Word → Helvetica with warning (Task 1); `render-cv` writes the PDF by default, `--ats-only`, `RenderedCv.pdf_path` (Tasks 2, 3); verification of title and heading order, removal on failure (Task 2); tests 1-5 from the spec map to Task 1 tests (A4, order, fallback/precedence, long CV, crash cases) and Task 3 CLI tests; side-by-side tuning (Task 3, step 6). One spec deviation, recorded here: `pypdf` is a **runtime** dependency (pinned in `requirements.txt`), not dev-only, because the service uses it to verify the PDF; it is small and pure Python.

**Placeholders:** none. The two conditional fixes in Task 1 step 4 are explicit and bounded.

**Type consistency:** `Fonts(regular, bold, italic)`, `HELVETICA`, `find_fonts(search_dirs)`, `write_pdf(doc, path, fonts)`, `extract_pdf_text(path)`, `render_cv_files(..., with_pdf, fonts)`, `RenderedCv.pdf_path` are used with these exact names in every task.

**Review Focus:** items 1-5 have tests in Task 1 (`test_markup_looking_text_prints_literally`, `test_a_corrupt_font_file_falls_back_instead_of_crashing`, `test_non_latin_text_with_the_fallback_font_does_not_crash`, `test_a_very_long_unbroken_token_and_a_very_long_bullet_do_not_crash`, `test_a_long_cv_flows_onto_more_pages_without_losing_text`); item 6 is `test_pdf_verification_ignores_whitespace_and_line_wrapping` plus the squash in `_pdf_problems` (Task 2). Stranded headings are prevented by `keepWithNext` on the heading and role styles and `KeepTogether`; this is covered by construction, not by a test.
