"""Apply a link / remove decision to a document (PLAN.md Step 19)."""

from __future__ import annotations

from core.artefacts.context import EvidencePack
from core.artefacts.schema import CoverLetter, Pitch, Sentence


class DecisionError(ValueError):
    """A decision cannot be applied (unknown sentence, bad action, bad link)."""


def _find(document: CoverLetter | Pitch, sentence_id: str) -> Sentence:
    """Find a sentence by id.

    Args:
        document: The document.
        sentence_id: The sentence's id.

    Returns:
        The sentence.

    Raises:
        DecisionError: If no such sentence exists or it is code-composed.
    """
    groups = (
        [p.sentences for p in document.paragraphs]
        if isinstance(document, CoverLetter)
        else [v.sentences for v in document.variants]
    )
    for sentences in groups:
        for sentence in sentences:
            if sentence.id == sentence_id:
                if sentence.kind == "composed":
                    raise DecisionError(
                        "the title lines are written by code and cannot be changed"
                    )
                return sentence
    raise DecisionError("this sentence no longer exists in the document")


def apply_decision(
    document: CoverLetter | Pitch,
    sentence_id: str,
    *,
    action: str,
    evidence_ref: str | None,
    pack: EvidencePack,
) -> CoverLetter | Pitch:
    """Apply a decision to a copy of a document.

    Args:
        document: The current document (never mutated).
        sentence_id: The sentence being decided.
        action: `link` (attach a source that evidences it) or `remove`.
        evidence_ref: For a link, an id from the evidence pack.
        pack: The evidence pack, to validate a link.

    Returns:
        The updated copy. A linked sentence becomes a traced claim; a removed
        one is dropped, and in a letter a paragraph left empty is dropped too.

    Raises:
        DecisionError: For an unknown sentence or action, a composed sentence,
            or a link to an id outside the pack.
    """
    if action not in ("link", "remove"):
        raise DecisionError(f"unknown action {action!r}")
    updated = document.model_copy(deep=True)
    target = _find(updated, sentence_id)
    if action == "link":
        if not evidence_ref or evidence_ref not in pack.items:
            raise DecisionError("link the sentence to a source from the evidence list")
        target.sources = [evidence_ref]
        target.kind = "claim"
        target.flag = "linked"
        return updated
    if isinstance(updated, CoverLetter):
        for paragraph in updated.paragraphs:
            paragraph.sentences = [
                s for s in paragraph.sentences if s.id != sentence_id
            ]
        updated.paragraphs = [p for p in updated.paragraphs if p.sentences]
    else:
        for variant in updated.variants:
            variant.sentences = [s for s in variant.sentences if s.id != sentence_id]
    return updated
