"""Plain-text views of a rendered CV: the `.txt` twin, and the text an ATS
would read out of the `.docx` (every paragraph in XML order)."""

from __future__ import annotations

import difflib
import zipfile
from pathlib import Path

from lxml import etree

from core.render.model import RenderDoc

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def render_text(doc: RenderDoc) -> str:
    """Render the document as plain text, one block per line.

    Args:
        doc: The render model.

    Returns:
        The text with a trailing newline.
    """
    return "\n".join(block.text for block in doc.blocks) + "\n"


def extract_docx_text(path: Path) -> str:
    """Read a `.docx` the way an ATS parser does: every paragraph, in the
    order it appears in the XML (not the visual order).

    Args:
        path: The `.docx` file.

    Returns:
        One paragraph per line with a trailing newline.
    """
    with zipfile.ZipFile(path) as package:
        root = etree.fromstring(package.read("word/document.xml"))
    lines = [
        "".join(node.text or "" for node in paragraph.iter(f"{{{_W}}}t"))
        for paragraph in root.iter(f"{{{_W}}}p")
    ]
    return "\n".join(lines) + "\n"


def diff_texts(expected: str, actual: str) -> list[str]:
    """Diff two texts line by line.

    Args:
        expected: The reference text.
        actual: The text to check.

    Returns:
        Unified-diff lines; empty when the texts are identical.
    """
    return list(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile="txt",
            tofile="docx",
            lineterm="",
        )
    )
