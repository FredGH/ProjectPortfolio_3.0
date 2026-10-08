"""Write the ATS .docx, its .txt twin and the designed PDF for one approved
tailored CV, and verify them (Step 18)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.render.docx_ats import write_docx
from core.render.format import build_filename
from core.render.model import RenderDoc, build_render_doc
from core.render.pdf_designed import Fonts, find_fonts, undrawable_chars, write_pdf
from core.render.text import (
    diff_texts,
    extract_docx_text,
    extract_pdf_text,
    render_text,
)
from core.tailoring.schema import TailoredDocument


class RenderError(Exception):
    """A generated file does not read back as intended."""


@dataclass(frozen=True)
class RenderedCv:
    """The files written for one CV.

    Attributes:
        docx_path: The ATS .docx.
        txt_path: The plain-text twin it was verified against.
        pdf_path: The designed PDF, or None when it was not requested.
        warnings: Things the user should know that did not stop the render.
    """

    docx_path: Path
    txt_path: Path
    pdf_path: Path | None = None
    warnings: tuple[str, ...] = ()


def _squash(text: str) -> str:
    """Remove all whitespace, so wrapping and spacing cannot matter.

    Args:
        text: Any text.

    Returns:
        The text with every whitespace character removed.
    """
    return re.sub(r"\s+", "", text)


def _pattern(text: str, missing: set[str]) -> re.Pattern[str]:
    """Build a whitespace-blind pattern for text, tolerant of undrawable characters.

    Args:
        text: The expected text.
        missing: Characters the font cannot draw; they may come back as a
            box, a NUL or nothing, so each matches zero or one character.

    Returns:
        A compiled regex over the text with all whitespace removed.
    """
    return re.compile(
        "".join(".?" if ch in missing else re.escape(ch) for ch in _squash(text))
    )


def _pdf_problems(
    render_doc: RenderDoc, pdf_path: Path, missing: set[str]
) -> list[str]:
    """Check that the PDF reads back with the exact title and headings in order.

    Args:
        render_doc: The model the PDF was written from.
        pdf_path: The written PDF.
        missing: Characters the PDF font cannot draw (tolerated in matching).

    Returns:
        A list of problems; empty when the PDF is as intended.
    """
    try:
        extracted = extract_pdf_text(pdf_path)
    except Exception as exc:  # pypdf raises assorted errors on a bad file
        return [f"the PDF cannot be read back: {exc}"]
    text = _squash(extracted)
    problems: list[str] = []
    if _pattern(render_doc.title, missing).search(text) is None:
        problems.append("the exact target title is missing from the PDF text")
    position = 0
    for heading in render_doc.headings():
        found = _pattern(heading, missing).search(text, position)
        if found is None:
            problems.append(f"PDF heading missing or out of order: {heading}")
            break
        position = found.end()
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
        The written paths, plus any warnings (characters the PDF font cannot
        draw; an older same-name PDF left untouched by an ATS-only run).

    Raises:
        ValueError: If the target title is blank (nothing is written).
        RenderError: If the text extracted from the .docx differs from the
            .txt twin, or the .docx or PDF lacks the exact target title, or
            the PDF headings are missing or out of order, or the PDF cannot
            be written at all; every written file is removed.
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
    warnings: list[str] = []
    if pdf_path is not None:
        try:
            resolved = fonts or find_fonts()
            missing = undrawable_chars(render_doc, resolved)
            if missing:
                warnings.append(
                    "the PDF font cannot draw these characters, so they show as "
                    f"blanks or boxes there: {' '.join(missing)}"
                )
            write_pdf(render_doc, pdf_path, resolved)
            problems.extend(_pdf_problems(render_doc, pdf_path, set(missing)))
        except Exception as exc:  # reportlab/pypdf/OS errors must not leave files
            problems.append(f"the PDF could not be written: {exc}")
    elif docx_path.with_suffix(".pdf").exists():
        warnings.append(
            "an older PDF with the same name was left untouched and does not "
            f"match these files: {docx_path.with_suffix('.pdf')}"
        )
    if problems:
        for written in (docx_path, txt_path, pdf_path):
            if written is not None:
                written.unlink(missing_ok=True)
        raise RenderError(
            "the output does not read back as intended:\n" + "\n".join(problems[:20])
        )
    return RenderedCv(
        docx_path=docx_path,
        txt_path=txt_path,
        pdf_path=pdf_path,
        warnings=tuple(warnings),
    )
