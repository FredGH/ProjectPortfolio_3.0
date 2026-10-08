"""Write the ATS .docx and .txt twin for one approved tailored CV, and verify
them against each other (Step 18a)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.render.docx_ats import write_docx
from core.render.format import build_filename
from core.render.model import build_render_doc
from core.render.text import diff_texts, extract_docx_text, render_text
from core.tailoring.schema import TailoredDocument


class RenderError(Exception):
    """The generated .docx does not read back as intended."""


@dataclass(frozen=True)
class RenderedCv:
    """The files written for one CV.

    Attributes:
        docx_path: The ATS .docx.
        txt_path: The plain-text twin it was verified against.
    """

    docx_path: Path
    txt_path: Path


def render_cv_files(
    tailored: TailoredDocument, company: str | None, out_dir: Path
) -> RenderedCv:
    """Render, write and verify the .docx and .txt for one CV.

    Args:
        tailored: The approved tailored document.
        company: The target employer for the filename, or None.
        out_dir: Directory to write into (created if missing).

    Returns:
        The two written paths.

    Raises:
        ValueError: If the target title is blank (nothing is written).
        RenderError: If the text extracted from the .docx differs from the
            .txt twin or lacks the exact target title; both files are
            removed.
    """
    render_doc = build_render_doc(tailored)
    out_dir.mkdir(parents=True, exist_ok=True)
    docx_path = out_dir / build_filename(
        tailored.identity, tailored.target_title, company, "docx"
    )
    txt_path = docx_path.with_suffix(".txt")
    expected = render_text(render_doc)
    write_docx(render_doc, docx_path)
    txt_path.write_text(expected, encoding="utf-8")
    extracted = extract_docx_text(docx_path)
    problems = diff_texts(expected, extracted)
    if render_doc.title not in extracted.splitlines():
        problems.append("the exact target title is missing from the .docx text")
    if problems:
        docx_path.unlink(missing_ok=True)
        txt_path.unlink(missing_ok=True)
        raise RenderError(
            "the .docx does not read back as intended:\n" + "\n".join(problems[:20])
        )
    return RenderedCv(docx_path=docx_path, txt_path=txt_path)
