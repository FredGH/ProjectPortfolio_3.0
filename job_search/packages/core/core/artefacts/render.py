"""Write a cover letter and a pitch to files (PLAN.md Step 19).

The letter reuses Step 18's writers: an ATS-safe single-column `.docx`, a
plain-text twin it is verified against, and a designed `.pdf`. The exact job
title is asserted on the **rendered** text. The pitch is a plain-text script.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.artefacts.schema import CoverLetter, Pitch, pitch_text, variant_text
from core.render.docx_ats import write_docx
from core.render.format import build_filename
from core.render.model import Block, RenderDoc
from core.render.pdf_designed import Fonts, find_fonts, undrawable_chars, write_pdf
from core.render.text import (
    diff_texts,
    extract_docx_text,
    extract_pdf_text,
    render_text,
)


class RenderError(Exception):
    """A generated file does not read back as intended."""


@dataclass(frozen=True)
class RenderedLetter:
    """The files written for one letter.

    Attributes:
        docx_path: The ATS `.docx`.
        txt_path: The plain-text twin it was verified against.
        pdf_path: The designed PDF, or None when not requested.
        warnings: Things the user should know that did not stop the render.
    """

    docx_path: Path
    txt_path: Path
    pdf_path: Path | None = None
    warnings: tuple[str, ...] = ()


def _squash(text: str) -> str:
    """Remove all whitespace, so wrapping cannot matter.

    Args:
        text: Any text.

    Returns:
        The text without whitespace.
    """
    return re.sub(r"\s+", "", text)


def _stem(identity: str, title: str, company: str | None, suffix: str) -> str:
    """Build a file stem such as `Surname_Title_Company_cover_letter`.

    Args:
        identity: The candidate's full name.
        title: The exact job title.
        company: The employer, or None.
        suffix: `cover_letter` or `pitch`.

    Returns:
        The stem, safe on any filesystem.
    """
    base = build_filename(identity, title, company, "x")[: -len(".x")]
    return f"{base}_{suffix}"


def letter_render_doc(letter: CoverLetter) -> RenderDoc:
    """Lay a letter out as plain blocks (one column, no tables, no images).

    Args:
        letter: The letter.

    Returns:
        A `RenderDoc` whose title is the exact job title.
    """
    blocks = [Block("name", letter.sender)]
    if letter.contact:
        blocks.append(Block("contact", letter.contact))
    blocks += [
        Block("paragraph", letter.date),
        Block("paragraph", letter.recipient),
        Block("headline", letter.reference),
    ]
    blocks += [
        Block("paragraph", " ".join(s.text for s in p.sentences))
        for p in letter.paragraphs
    ]
    blocks += [Block("paragraph", line) for line in letter.sign_off.split("\n")]
    return RenderDoc(title=letter.target_title, blocks=tuple(blocks))


def render_cover_letter_files(
    letter: CoverLetter,
    out_dir: Path,
    *,
    with_pdf: bool = True,
    fonts: Fonts | None = None,
) -> RenderedLetter:
    """Write and verify the letter's `.docx`, `.txt` twin and `.pdf`.

    Args:
        letter: The approved letter.
        out_dir: Directory to write into (created if missing).
        with_pdf: Also write the designed PDF.
        fonts: Fonts for the PDF; found automatically when omitted.

    Returns:
        The written paths and any warnings.

    Raises:
        RenderError: If the text read back from the `.docx` differs from the
            `.txt`, the exact title is missing from the `.docx` or `.pdf` text,
            or the PDF cannot be written; every written file is removed.
    """
    doc = letter_render_doc(letter)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _stem(
        letter.sender, letter.target_title, letter.company or None, "cover_letter"
    )
    docx_path, txt_path = out_dir / f"{stem}.docx", out_dir / f"{stem}.txt"
    pdf_path = out_dir / f"{stem}.pdf" if with_pdf else None
    expected = render_text(doc)
    write_docx(doc, docx_path)
    txt_path.write_text(expected, encoding="utf-8")
    extracted = extract_docx_text(docx_path)
    problems = diff_texts(expected, extracted)
    if f"Re: {letter.target_title}" not in extracted:
        problems.append(
            "the exact target title is missing from the .docx reference line"
        )
    if extracted.count(letter.target_title) < 2:
        problems.append(
            "the exact target title must appear in the reference line and the "
            "opening sentence"
        )
    warnings: list[str] = []
    if pdf_path is not None:
        try:
            resolved = fonts or find_fonts()
            missing = undrawable_chars(doc, resolved)
            if missing:
                warnings.append(
                    "the PDF font cannot draw these characters, so they show as "
                    f"blanks or boxes there: {' '.join(missing)}"
                )
            write_pdf(doc, pdf_path, resolved)
            if _squash(f"Re: {letter.target_title}") not in _squash(
                extract_pdf_text(pdf_path)
            ):
                problems.append("the exact target title is missing from the PDF text")
        except (
            Exception
        ) as exc:  # noqa: BLE001 - reportlab/pypdf/OS errors must not leave files
            problems.append(f"the PDF could not be written: {exc}")
    if problems:
        for written in (docx_path, txt_path, pdf_path):
            if written is not None:
                written.unlink(missing_ok=True)
        raise RenderError(
            "the output does not read back as intended:\n" + "\n".join(problems[:20])
        )
    return RenderedLetter(docx_path, txt_path, pdf_path, tuple(warnings))


def render_pitch_file(pitch: Pitch, out_dir: Path) -> Path:
    """Write the pitch script as a text file.

    Args:
        pitch: The approved pitch.
        out_dir: Directory to write into (created if missing).

    Returns:
        The written path.

    Raises:
        RenderError: If any variant lacks the exact job title (nothing is
            written).
    """
    for variant in pitch.variants:
        if pitch.target_title not in variant_text(variant):
            raise RenderError(
                f"the {variant.seconds}s pitch does not name the exact title "
                f"{pitch.target_title!r}"
            )
    out_dir.mkdir(parents=True, exist_ok=True)
    name = _stem(pitch.sender, pitch.target_title, pitch.company or None, "pitch")
    path = out_dir / f"{name}.txt"
    path.write_text(pitch_text(pitch), encoding="utf-8")
    return path


_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


def letter_download_bundle(
    letter: CoverLetter, *, fonts: Fonts | None = None
) -> list[tuple[str, str, bytes]]:
    """Render a letter in memory for the UI.

    Args:
        letter: The approved letter.
        fonts: Fonts for the PDF; found automatically when omitted.

    Returns:
        `(name, mime, bytes)` for the PDF, the `.docx` and the `.txt`.

    Raises:
        RenderError: If a file does not read back as intended.
    """
    with tempfile.TemporaryDirectory() as tmp:
        files = render_cover_letter_files(letter, Path(tmp), fonts=fonts)
        paths = [files.pdf_path, files.docx_path, files.txt_path]
        return [
            (p.name, _MIME[p.suffix], p.read_bytes()) for p in paths if p is not None
        ]


def pitch_download_bundle(pitch: Pitch) -> list[tuple[str, str, bytes]]:
    """Render a pitch in memory for the UI.

    Args:
        pitch: The approved pitch.

    Returns:
        `(name, mime, bytes)` for the text script.

    Raises:
        RenderError: If a variant lacks the exact title.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = render_pitch_file(pitch, Path(tmp))
        return [(path.name, _MIME[".txt"], path.read_bytes())]
