"""Write the ATS .docx, its .txt twin and the designed PDF for one approved
tailored CV, and verify them (Step 18)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.render.docx_ats import write_docx
from core.render.format import build_filename
from core.render.model import RenderDoc, build_render_doc
from core.render.pdf_designed import Fonts, write_pdf
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
