"""Anki export (PLAN.md Step 20): a tab-separated file Anki imports directly."""

from __future__ import annotations

import html
import re

from core.interview.progress import Progress
from core.interview.store import StoredQuestion

HEADER = "#separator:tab\n#html:true\n#tags column:3\n"


def _field(text: str) -> str:
    """Make text safe for one tab-separated, HTML-enabled field.

    Args:
        text: Any text.

    Returns:
        The text HTML-escaped, with tabs as spaces and newlines as `<br>`.
    """
    return (
        html.escape(text).replace("\t", " ").replace("\r\n", "\n").replace("\n", "<br>")
    )


def _tag(text: str) -> str:
    """Make a tag with no spaces.

    Args:
        text: A topic or tag.

    Returns:
        Words joined by underscores.
    """
    return "_".join(re.findall(r"[A-Za-z0-9]+", text))


def export_anki(
    questions: list[StoredQuestion], progress: dict[object, Progress]
) -> str:
    """Build the Anki import file for approved questions.

    Args:
        questions: The questions to export, in the order to write them.
        progress: The user's progress by question id.

    Returns:
        A header plus one note per question: the question, then the user's
        own answer when they have one (else the model answer), then tags.
    """
    lines = [HEADER.rstrip("\n")]
    for question in questions:
        mine = progress.get(question.id)
        answer = (
            mine.personal_answer
            if mine and mine.personal_answer and mine.answer_status != "needs_review"
            else question.model_answer
        )
        tags = " ".join(
            t for t in [_tag(question.topic), *map(_tag, question.tags)] if t
        )
        lines.append("\t".join([_field(question.question), _field(answer), tags]))
    return "\n".join(lines) + "\n"
