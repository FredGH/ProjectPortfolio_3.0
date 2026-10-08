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
        (
            f'<font color="{_BLUE_HEX}"><u>{part}</u></font>'
            if "linkedin.com" in part
            else part
        )
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
