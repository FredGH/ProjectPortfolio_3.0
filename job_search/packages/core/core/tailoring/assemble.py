"""Deterministic assembly of a TailoredDocument (Step 17, approach B:
"assemble, don't generate").

Code builds the document from the truth base. The Tailor's output only
chooses bullet order/wording and skills; companies, titles and dates are
copied by index and never pass through the model, so "no experience title
differs from the truth base" holds by construction.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from core.cv.schema import CVTruthBase
from core.tailoring.schema import (
    MAX_SUMMARY_HIGHLIGHTS,
    BulletOrigin,
    JobSkill,
    TailoredBullet,
    TailoredDocument,
    TailoredExperience,
    TailoredSummary,
    TailorExperience,
    TailorOutput,
)

logger = logging.getLogger(__name__)

# A leading decorative bullet glyph, or a "- " dash-then-space marker. A
# bare leading minus ("-5% churn") is a number, not a marker, so a dash
# only counts when whitespace follows it.
_LEADING_MARKER_RE = re.compile(r"^\s*(?:[•▪●◦■–—*·]+\s*|-\s+)")

# Markdown emphasis around a word or phrase: **x**, __x__, *x*, _x_. The
# marker must hug non-space text on both sides, and a single `_`/`*` must
# not touch a word character outside it, so "3 * 4", "2x*" and
# "dim_job_score" are left alone.
_STRONG_RE = re.compile(r"(\*\*|__)(\S(?:.*?\S)?)\1")
_EMPHASIS_RE = re.compile(
    r"(?<![\w*])\*(\S(?:[^*]*?\S)?)\*(?![\w*])|(?<![\w_])_(\S(?:[^_]*?\S)?)_(?![\w_])"
)

# Symbols (Unicode "So") that carry meaning in a CV and are kept.
_KEPT_SYMBOLS = frozenset("°©®™℠℃℉")

# Invisible emoji parts: variation selectors, zero-width joiner, and the
# combining keycap. Left behind they corrupt the text an ATS reads.
_INVISIBLE = frozenset([chr(c) for c in range(0xFE00, 0xFE10)] + ["\u200d", "\u20e3"])


def _normalise(text: str) -> str:
    """Lowercase and collapse whitespace for comparison.

    Args:
        text: Any text.

    Returns:
        The normalised text.
    """
    return re.sub(r"\s+", " ", text.strip().lower())


def clean_text(text: str) -> str:
    """Mechanically enforce content-level ATS hygiene on generated text.

    Removes markdown emphasis markers, a leading bullet glyph, decorative
    symbols and emoji (including their invisible joiners and variation
    selectors), and collapses whitespace. Meaningful symbols such as °, ©,
    ® and ™ are kept. Done in code rather than asked of the model
    (PLAN.md Step 18: enforce mechanically, not by prompting).

    Args:
        text: Generated text.

    Returns:
        The cleaned text; may be empty.
    """
    unemphasised = _STRONG_RE.sub(r"\2", text.strip())
    unemphasised = _EMPHASIS_RE.sub(
        lambda m: m.group(1) if m.group(1) is not None else m.group(2), unemphasised
    )
    stripped = _LEADING_MARKER_RE.sub("", unemphasised)
    kept = "".join(
        ch
        for ch in stripped
        if ch not in _INVISIBLE
        and (
            ch in _KEPT_SYMBOLS
            or (unicodedata.category(ch) != "So" and not 0x1F000 <= ord(ch) <= 0x1FAFF)
        )
    )
    return re.sub(r"\s+", " ", kept).strip()


def bullet_index(truth_base: CVTruthBase) -> dict[str, tuple[int, str]]:
    """Index every truth-base bullet by its ID.

    Args:
        truth_base: The truth base.

    Returns:
        A map of `bullet_id` to `(experience_index, text)`.
    """
    return {
        bullet.bullet_id: (index, bullet.text)
        for index, experience in enumerate(truth_base.experience)
        for bullet in experience.bullets
    }


def classify_origin(
    text: str, refs: list[str], known: dict[str, tuple[int, str]]
) -> BulletOrigin:
    """Classify a generated line against the truth base.

    Args:
        text: The generated text.
        refs: The `bullet_id`s the Tailor cited.
        known: The result of `bullet_index`.

    Returns:
        `orphan` when no cited ID exists; `original` when exactly one
        valid ID is cited and the text matches that bullet (ignoring case
        and spacing); otherwise `reworded`.
    """
    valid = [ref for ref in refs if ref in known]
    if not valid:
        return "orphan"
    if len(valid) == 1 and _normalise(text) == _normalise(known[valid[0]][1]):
        return "original"
    return "reworded"


def _summary_highlights(text: str, phrases: list[str]) -> list[str]:
    """Keep the highlight phrases that really occur in the summary.

    Args:
        text: The final summary text.
        phrases: The phrases the Tailor proposed.

    Returns:
        At most `MAX_SUMMARY_HIGHLIGHTS` phrases, each copied from `text`
        (matched ignoring case), in text order, without overlaps.
    """
    lowered = text.casefold()
    found: list[tuple[int, int]] = []
    for phrase in phrases:
        needle = " ".join(phrase.split()).casefold()
        start = lowered.find(needle) if needle else -1
        if start >= 0:
            found.append((start, start + len(needle)))
    chosen: list[tuple[int, int]] = []
    for start, end in sorted(set(found)):
        if not chosen or start >= chosen[-1][1]:
            chosen.append((start, end))
    return [text[start:end] for start, end in chosen[:MAX_SUMMARY_HIGHLIGHTS]]


def _assemble_summary(
    truth_base: CVTruthBase,
    output: TailorOutput,
    known: dict[str, tuple[int, str]],
) -> TailoredSummary | None:
    """Build the summary section.

    Args:
        truth_base: The truth base.
        output: The Tailor's output.
        known: The result of `bullet_index`.

    Returns:
        The tailored summary, the truth-base summary as `original` when the
        model gave none, or None when neither exists.
    """
    if output.summary is not None:
        text = clean_text(output.summary.text)
        if text:
            refs = list(output.summary.evidence_refs)
            if truth_base.summary and _normalise(text) == _normalise(
                truth_base.summary
            ):
                origin: BulletOrigin = "original"
            elif truth_base.summary or any(ref in known for ref in refs):
                # An adaptation of the candidate's own summary needs no
                # bullet citation; the critic judges it against that summary.
                origin = "reworded"
            else:
                origin = "orphan"
            return TailoredSummary(
                text=text,
                evidence_refs=refs,
                origin=origin,
                highlights=_summary_highlights(text, output.summary.highlights),
            )
    if truth_base.summary:
        return TailoredSummary(
            text=truth_base.summary, evidence_refs=[], origin="original"
        )
    return None


def _highlighted_skills(
    truth_base: CVTruthBase,
    output: TailorOutput,
    job_skills: list[JobSkill],
) -> list[str]:
    """Pick the truth-base skills to emphasise.

    Args:
        truth_base: The truth base.
        output: The Tailor's output; its `skills` are the ones it judged
            relevant.
        job_skills: The skills the job asks for.

    Returns:
        Names of truth-base skills that the Tailor chose or that match a job
        skill (same canonical id, or same name), in truth-base order.
    """
    chosen = {name.strip().casefold() for name in output.skills}
    job_ids = {js.skill_id for js in job_skills}
    job_labels = {js.label.strip().casefold() for js in job_skills}
    names: list[str] = []
    for skill in truth_base.skills:
        name = skill.name.strip()
        if (
            name.casefold() in chosen
            or name.casefold() in job_labels
            or (skill.canonical_id and skill.canonical_id in job_ids)
        ) and name not in names:
            names.append(name)
    return names


def assemble(
    truth_base: CVTruthBase,
    output: TailorOutput,
    *,
    target_title: str,
    job_skills: list[JobSkill] | None = None,
) -> TailoredDocument:
    """Build the tailored document.

    Args:
        truth_base: The user's CV truth base.
        output: The Tailor's parsed output.
        target_title: `title_for_display`, injected as the headline.
        job_skills: The skills the job asks for, used to pick which skills
            to highlight.

    Returns:
        A `TailoredDocument` listing every truth-base skill, with the
        relevant ones named in `highlighted_skills`. Roles are always the truth base's, in order.
        A `keep` item becomes the truth-base bullet's original text. A
        role the model omitted keeps its original bullets; a role index
        outside the truth base is ignored; for a repeated role index the
        first entry wins.
    """
    known = bullet_index(truth_base)
    by_role: dict[int, TailorExperience] = {}
    for entry in output.experience:
        if 0 <= entry.truth_index < len(truth_base.experience):
            by_role.setdefault(entry.truth_index, entry)

    experience: list[TailoredExperience] = []
    for index, role in enumerate(truth_base.experience):
        chosen = by_role.get(index)
        if chosen is None:
            bullets = [
                TailoredBullet(
                    text=b.text, evidence_refs=[b.bullet_id], origin="original"
                )
                for b in role.bullets
            ]
        else:
            bullets = []
            kept: set[str] = set()
            for item in chosen.bullets:
                if item.keep is not None:
                    # An unchanged bullet: the truth base's own text. An
                    # unknown id is dropped (nothing to fabricate); an id
                    # from another role is still placed, so the evidence
                    # check flags it like any cross-role citation.
                    if item.keep not in known:
                        logger.debug("dropping unknown keep id %r", item.keep)
                    elif item.keep not in kept:
                        kept.add(item.keep)
                        bullets.append(
                            TailoredBullet(
                                text=known[item.keep][1],
                                evidence_refs=[item.keep],
                                origin="original",
                            )
                        )
                    continue
                text = clean_text(item.text or "")
                if not text:
                    continue
                refs = list(item.evidence_refs)
                bullets.append(
                    TailoredBullet(
                        text=text,
                        evidence_refs=refs,
                        origin=classify_origin(text, refs, known),
                    )
                )
        experience.append(
            TailoredExperience(
                truth_index=index,
                company=role.company,
                title=role.title,
                start=role.start,
                end=role.end,
                tech=list(role.tech),
                bullets=bullets,
            )
        )

    highlighted = _highlighted_skills(truth_base, output, job_skills or [])

    return TailoredDocument(
        target_title=target_title,
        headline=target_title,
        identity=truth_base.identity,
        email=truth_base.email,
        phone=truth_base.phone,
        linkedin_url=truth_base.linkedin_url,
        nationality=truth_base.nationality,
        work_auth=truth_base.work_auth,
        locations=list(truth_base.locations),
        summary=_assemble_summary(truth_base, output, known),
        experience=experience,
        skills=list(truth_base.skills),
        highlighted_skills=highlighted,
        projects=list(truth_base.projects),
        publications=list(truth_base.publications),
        education=list(truth_base.education),
        qualifications=list(truth_base.qualifications),
        activities=list(truth_base.activities),
    )
