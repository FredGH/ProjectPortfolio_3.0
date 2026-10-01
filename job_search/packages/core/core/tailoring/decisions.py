"""The link/reject rules for orphan lines (Step 17, spec "Orphan decisions").

Pure functions over a TailoredDocument — no database. The store applies the
result; this module only decides what the document becomes.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.cv.schema import CVTruthBase
from core.tailoring.assemble import bullet_index
from core.tailoring.checks import compute_keyword_coverage
from core.tailoring.schema import (
    JobSkill,
    TailoredBullet,
    TailoredDocument,
    TailoredSummary,
)
from core.tailoring.store import StoredOrphan


class DecisionError(ValueError):
    """A decision cannot be applied (bad action, bad link, stale orphan)."""


@dataclass(frozen=True)
class DecisionResult:
    """The outcome of one decision.

    Attributes:
        document: The document after the decision (a copy; the input is
            never mutated).
        removed_position: `(experience_index, bullet_index)` when a line was
            removed, so later orphans in that role can be re-indexed.
    """

    document: TailoredDocument
    removed_position: tuple[int, int] | None


def _bullet_at(document: TailoredDocument, orphan: StoredOrphan) -> TailoredBullet:
    """Find the experience bullet an orphan refers to.

    Args:
        document: The current document.
        orphan: The orphan row.

    Returns:
        The bullet at the orphan's position.

    Raises:
        DecisionError: If the position does not exist or its text no longer
            matches what was recorded (the document changed since).
    """
    role_index, position = orphan.experience_index, orphan.bullet_index
    if (
        role_index is None
        or position is None
        or not 0 <= role_index < len(document.experience)
        or not 0 <= position < len(document.experience[role_index].bullets)
    ):
        raise DecisionError("this line no longer exists in the document")
    bullet = document.experience[role_index].bullets[position]
    if bullet.text != orphan.text:
        raise DecisionError("the document has changed since this line was recorded")
    return bullet


def apply_decision(
    document: TailoredDocument,
    orphan: StoredOrphan,
    *,
    action: str,
    evidence_ref: str | None,
    truth_base: CVTruthBase,
    job_skills: list[JobSkill] | None = None,
    pending_locations: frozenset[str] = frozenset(),
) -> DecisionResult:
    """Apply a link or reject decision to a document.

    Args:
        document: The current document.
        orphan: The orphan being decided.
        action: `link` or `reject`.
        evidence_ref: For `link`, the truth-base `bullet_id` that evidences
            the line.
        truth_base: The truth base the document was built from.
        job_skills: The job's skills. When given, the resulting document's
            keyword coverage is recomputed so it never goes stale.
        pending_locations: Locations (in the *resulting* document) of the
            run's other lines still awaiting a decision; they never count
            as coverage.

    Returns:
        The updated document and any removed position.

    Raises:
        DecisionError: On an unknown action; a link with no ref, an
            unknown ref, or (for an experience line) a ref from another
            role; or a stale/out-of-range orphan.
    """
    result = _apply(document, orphan, action, evidence_ref, truth_base)
    if job_skills is None:
        return result
    coverage = compute_keyword_coverage(
        result.document, truth_base, job_skills, exclude=pending_locations
    )
    return DecisionResult(
        result.document.model_copy(update={"keyword_coverage": coverage}),
        result.removed_position,
    )


def _apply(
    document: TailoredDocument,
    orphan: StoredOrphan,
    action: str,
    evidence_ref: str | None,
    truth_base: CVTruthBase,
) -> DecisionResult:
    """Apply the link/reject rules (see `apply_decision`), without coverage.

    Args:
        document: The current document.
        orphan: The orphan being decided.
        action: `link` or `reject`.
        evidence_ref: For `link`, the evidencing truth-base `bullet_id`.
        truth_base: The truth base the document was built from.

    Returns:
        The updated document and any removed position.

    Raises:
        DecisionError: See `apply_decision`.
    """
    if action not in ("link", "reject"):
        raise DecisionError(f"unknown action {action!r}")
    known = bullet_index(truth_base)
    updated = document.model_copy(deep=True)

    if orphan.section == "summary":
        summary = updated.summary
        if summary is None or summary.text != orphan.text:
            raise DecisionError("the summary has changed since this line was recorded")
        if action == "link":
            if evidence_ref is None or evidence_ref not in known:
                raise DecisionError("link needs an existing truth-base bullet id")
            summary.evidence_refs = [evidence_ref]
            summary.origin = "linked"
        elif truth_base.summary:
            updated.summary = TailoredSummary(
                text=truth_base.summary, evidence_refs=[], origin="original"
            )
        else:
            updated.summary = None
        return DecisionResult(updated, None)

    bullet = _bullet_at(updated, orphan)
    role_index = orphan.experience_index
    position = orphan.bullet_index
    assert role_index is not None and position is not None

    if action == "link":
        if evidence_ref is None or evidence_ref not in known:
            raise DecisionError("link needs an existing truth-base bullet id")
        if known[evidence_ref][0] != role_index:
            raise DecisionError("the evidence must come from the same role as the line")
        bullet.evidence_refs = [evidence_ref]
        bullet.origin = "linked"
        return DecisionResult(updated, None)

    valid = [
        ref
        for ref in orphan.claimed_refs
        if ref in known and known[ref][0] == role_index
    ]
    if orphan.kind == "unsupported" and len(valid) == 1:
        bullet.text = known[valid[0]][1]
        bullet.evidence_refs = [valid[0]]
        bullet.origin = "original"
        return DecisionResult(updated, None)

    updated.experience[role_index].bullets.pop(position)
    return DecisionResult(updated, (role_index, position))
