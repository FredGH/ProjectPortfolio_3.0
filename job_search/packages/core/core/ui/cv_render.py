"""Render an approved tailored CV into downloadable files for the UI
(Step 18): the same files `render-cv` writes, kept in memory instead of on
disk so nothing personal is left in the repo."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.render.service import render_cv_files
from core.tailoring.schema import TailoredDocument

_MIME = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".txt": "text/plain",
}


@dataclass(frozen=True)
class DownloadFile:
    """One file ready to download.

    Attributes:
        name: The filename (`<surname>_<title>_<company>.<ext>`).
        mime: Its content type.
        data: Its bytes.
    """

    name: str
    mime: str
    data: bytes


@dataclass(frozen=True)
class DownloadBundle:
    """The rendered files for one CV.

    Attributes:
        files: The PDF, the .docx and the .txt, in that order.
        warnings: Things the user should know that did not stop the render.
    """

    files: tuple[DownloadFile, ...]
    warnings: tuple[str, ...]


def render_for_download(document: dict, company: str | None) -> DownloadBundle:
    """Render a tailored CV to PDF, .docx and .txt in memory.

    Args:
        document: The approved run's `document`, as the API returns it.
        company: The target employer for the filenames, or None.

    Returns:
        The three files and any warnings.

    Raises:
        ValueError: If the document is invalid or its title is blank.
        RenderError: If a generated file does not read back as intended.
    """
    tailored = TailoredDocument.model_validate(document)
    with tempfile.TemporaryDirectory() as tmp:
        written = render_cv_files(tailored, company, Path(tmp))
        paths = [written.pdf_path, written.docx_path, written.txt_path]
        files = tuple(
            DownloadFile(path.name, _MIME[path.suffix], path.read_bytes())
            for path in paths
            if path is not None
        )
    return DownloadBundle(files=files, warnings=written.warnings)
