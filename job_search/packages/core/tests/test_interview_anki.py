"""Unit tests for the Anki export."""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime

from core.interview.anki import export_anki
from core.interview.progress import Progress
from core.interview.sm2 import Sm2State
from core.interview.store import StoredQuestion

_ID1, _ID2 = uuid.UUID(int=1), uuid.UUID(int=2)


def _q(
    qid: uuid.UUID, question: str, answer: str, topic: str, tags: list[str]
) -> StoredQuestion:
    """Build a stored question.

    Args:
        qid: Its id.
        question: The question.
        answer: The model answer.
        topic: Its topic.
        tags: Its tags.

    Returns:
        A `StoredQuestion`.
    """
    return StoredQuestion(
        qid,
        "c",
        topic,
        "technical",
        3,
        question,
        answer,
        tags,
        "generated",
        "approved",
        None,
        datetime(2026, 10, 15, tzinfo=UTC),
    )


def _mine(qid: uuid.UUID, answer: str | None, status: str) -> Progress:
    """Build a progress row.

    Args:
        qid: The question id.
        answer: The personal answer.
        status: The answer status.

    Returns:
        A `Progress`.
    """
    return Progress(qid, answer, status, Sm2State(2.5, 0, 0), None, None, None)


class TestExportAnki(unittest.TestCase):
    """The file imports into Anki as tab-separated notes."""

    def test_exact_output_for_a_small_bank(self) -> None:
        questions = [
            _q(
                _ID1,
                "What is a CTE?",
                "A named\nsubquery <b>.",
                "SQL and data modelling",
                ["window functions", "sql"],
            ),
            _q(_ID2, "Explain DAGs.", "Directed acyclic graphs.", "Pipelines", []),
        ]
        out = export_anki(questions, {_ID2: _mine(_ID2, "My own words.", "written")})
        self.assertEqual(
            out,
            "#separator:tab\n#html:true\n#tags column:3\n"
            "What is a CTE?\tA named<br>subquery &lt;b&gt;.\t"
            "SQL_and_data_modelling window_functions sql\n"
            "Explain DAGs.\tMy own words.\tPipelines\n",
        )

    def test_a_draft_still_under_review_is_not_exported_as_the_answer(self) -> None:
        questions = [_q(_ID1, "Q?", "Model.", "T", [])]
        out = export_anki(
            questions, {_ID1: _mine(_ID1, "Unchecked draft.", "needs_review")}
        )
        self.assertIn("\tModel.\t", out)
        self.assertNotIn("Unchecked", out)

    def test_tabs_in_text_cannot_break_the_columns(self) -> None:
        out = export_anki([_q(_ID1, "A\tB?", "x\ty", "T", [])], {})
        self.assertEqual(out.splitlines()[-1].count("\t"), 2)


if __name__ == "__main__":
    unittest.main()
