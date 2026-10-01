"""Code-only checks on an assembled TailoredDocument (Step 17).

Everything that can be verified mechanically is verified here, not left
to an LLM. The critic (core.tailoring.critic) covers only the semantic
gap: does a reworded bullet's claim follow from its cited sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.cv.schema import CVTruthBase
from core.tailoring.assemble import bullet_index
from core.tailoring.schema import JobSkill, KeywordCoverage, TailoredDocument

STRUCTURAL_CODES = frozenset(
    {"experience_changed", "headline_mismatch", "missing_target_title"}
)
"""Problems that cannot be shown to the user as an orphan bullet — they
mean the document itself is wrong, so the run must fail."""


@dataclass(frozen=True)
class Problem:
    """One problem found by a code check.

    Attributes:
        code: Machine-readable kind, e.g. `cross_role_evidence`.
        message: Human-readable explanation, also fed back to the Tailor.
        location: `summary`, `e{role}b{bullet}`, `e{role}` or `headline`.
    """

    code: str
    message: str
    location: str


def check_evidence_refs(
    document: TailoredDocument, truth_base: CVTruthBase
) -> list[Problem]:
    """Check every cited `evidence_ref` exists and stays within its role.

    A bullet with no refs at all is an orphan (see its `origin`), not a
    problem here. A bullet citing a bullet from a *different* role is
    flagged: that moves an achievement to another employer.

    Args:
        document: The assembled document.
        truth_base: The truth base it was assembled from.

    Returns:
        One `Problem` per bad reference.
    """
    known = bullet_index(truth_base)
    problems: list[Problem] = []
    if document.summary is not None:
        for ref in document.summary.evidence_refs:
            if ref not in known:
                problems.append(
                    Problem(
                        "invalid_evidence_ref",
                        f"the summary cites unknown bullet id {ref!r}",
                        "summary",
                    )
                )
    for role in document.experience:
        for position, bullet in enumerate(role.bullets):
            location = f"e{role.truth_index}b{position}"
            for ref in bullet.evidence_refs:
                if ref not in known:
                    problems.append(
                        Problem(
                            "invalid_evidence_ref",
                            f"bullet {location} cites unknown bullet id {ref!r}",
                            location,
                        )
                    )
                elif known[ref][0] != role.truth_index:
                    problems.append(
                        Problem(
                            "cross_role_evidence",
                            f"bullet {location} cites {ref!r} from another role; "
                            "a bullet may only cite bullets from its own role",
                            location,
                        )
                    )
    return problems


def check_experience_unchanged(
    document: TailoredDocument, truth_base: CVTruthBase
) -> list[Problem]:
    """Assert no role's company, title or dates differ from the truth base.

    Assembly makes this true by construction; the check is kept as defence
    in depth (PLAN.md Step 17: the second assertion "is the one that
    matters").

    Args:
        document: The assembled document.
        truth_base: The truth base.

    Returns:
        One `Problem` per differing or missing role.
    """
    problems: list[Problem] = []
    if len(document.experience) != len(truth_base.experience):
        problems.append(
            Problem(
                "experience_changed",
                f"the document has {len(document.experience)} roles, "
                f"the truth base has {len(truth_base.experience)}",
                "e0",
            )
        )
    for index, truth_role in enumerate(truth_base.experience):
        if index >= len(document.experience):
            break
        role = document.experience[index]
        if (role.company, role.title, role.start, role.end) != (
            truth_role.company,
            truth_role.title,
            truth_role.start,
            truth_role.end,
        ):
            problems.append(
                Problem(
                    "experience_changed",
                    f"role {index} differs from the truth base "
                    f"({role.title} at {role.company})",
                    f"e{index}",
                )
            )
    return problems


def check_headline(document: TailoredDocument) -> list[Problem]:
    """Assert the headline is exactly the injected target title.

    Args:
        document: The assembled document.

    Returns:
        A `missing_target_title` problem when there is no title to mirror,
        else a `headline_mismatch` problem when the headline differs from
        it in any way (including case).
    """
    if not document.target_title.strip():
        return [
            Problem(
                "missing_target_title", "there is no target title to mirror", "headline"
            )
        ]
    if document.headline != document.target_title:
        return [
            Problem(
                "headline_mismatch",
                f"the headline {document.headline!r} is not exactly the target "
                f"title {document.target_title!r}",
                "headline",
            )
        ]
    return []


def _mentions(text: str, label: str) -> bool:
    """Whether `label` appears in `text` as a whole word or phrase.

    Args:
        text: Text to search, already case-folded.
        label: The skill label.

    Returns:
        True on a whole-word, case-insensitive match.
    """
    pattern = rf"(?<!\w){re.escape(label.casefold())}(?!\w)"
    return re.search(pattern, text) is not None


def compute_keyword_coverage(
    document: TailoredDocument,
    truth_base: CVTruthBase,
    job_skills: list[JobSkill],
) -> KeywordCoverage:
    """Split a job's skills into covered / missing-but-evidenced / unevidenced.

    Only the *evidenced* gap is ever asked of the Tailor; asking it to
    surface a skill the CV does not evidence would invite fabrication.

    Args:
        document: The assembled document.
        truth_base: The truth base (the source of evidence).
        job_skills: The skills the job asks for.

    Returns:
        A `KeywordCoverage` of skill labels.
    """
    pieces = [bullet.text for role in document.experience for bullet in role.bullets]
    if document.summary is not None:
        pieces.append(document.summary.text)
    pieces.extend(skill.name for skill in document.skills)
    document_text = " ".join(pieces).casefold()
    document_ids = {s.canonical_id for s in document.skills if s.canonical_id}

    truth_text = " ".join(
        bullet.text for role in truth_base.experience for bullet in role.bullets
    ).casefold()
    truth_ids = {s.canonical_id for s in truth_base.skills if s.canonical_id}
    truth_names = {s.name.casefold() for s in truth_base.skills}

    covered: list[str] = []
    missing_evidenced: list[str] = []
    missing_unevidenced: list[str] = []
    for job_skill in job_skills:
        label = job_skill.label
        if job_skill.skill_id in document_ids or _mentions(document_text, label):
            covered.append(label)
        elif (
            job_skill.skill_id in truth_ids
            or label.casefold() in truth_names
            or _mentions(truth_text, label)
        ):
            missing_evidenced.append(label)
        else:
            missing_unevidenced.append(label)
    return KeywordCoverage(
        covered=covered,
        missing_evidenced=missing_evidenced,
        missing_unevidenced=missing_unevidenced,
    )
