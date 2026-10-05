"""Models for Step 17's tailored CV (docs/superpowers/specs/
2026-10-01-step17-tailoring-design.md).

Two families live here:

- `Tailor*` — the shape of what the Tailor LLM returns. Deliberately has
  no company, title or date fields: those never pass through the model.
- `Tailored*` / `TailoredDocument` — the assembled document, the contract
  between Step 17 and the Step 18 renderers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from core.cv.schema import (
    Activity,
    Certification,
    Education,
    Project,
    Publication,
    Skill,
)
from core.llm.json_response import parse_json_response

BulletOrigin = Literal["original", "reworded", "orphan", "linked"]
"""How a generated line relates to the truth base.

`original`: text identical to its single source bullet. `reworded`:
changed text with at least one valid source (the critic judges it).
`orphan`: no valid source. `linked`: an orphan the user attached to an
existing truth-base bullet.
"""


class TailoredBullet(BaseModel):
    """One bullet in the tailored document.

    Attributes:
        text: The bullet text as it will appear.
        evidence_refs: `bullet_id`s of the truth-base bullets it draws on.
        origin: How it relates to the truth base.
    """

    text: str
    evidence_refs: list[str] = Field(default_factory=list)
    origin: BulletOrigin


class TailoredSummary(BaseModel):
    """The tailored professional summary.

    Attributes:
        text: The summary text.
        evidence_refs: `bullet_id`s that support it.
        origin: How it relates to the truth base.
    """

    text: str
    evidence_refs: list[str] = Field(default_factory=list)
    origin: BulletOrigin


class TailoredExperience(BaseModel):
    """One role in the tailored document.

    Attributes:
        truth_index: This role's index in the truth base.
        company: Employer — copied from the truth base, never generated.
        title: Job title held — copied from the truth base, never generated.
        start: Start date, copied from the truth base.
        end: End date, copied from the truth base.
        tech: Technologies, copied from the truth base.
        bullets: The tailored bullets for this role.
    """

    truth_index: int
    company: str
    title: str
    start: str | None = None
    end: str | None = None
    tech: list[str] = Field(default_factory=list)
    bullets: list[TailoredBullet] = Field(default_factory=list)


class StretchAssessment(BaseModel):
    """Whether the target title outreaches the truth base's evidence.

    Attributes:
        is_stretch: True when the title implies seniority or scope the
            truth base does not evidence. Advisory only — never a failure.
        reason: One sentence explaining the judgement.
    """

    is_stretch: bool = False
    reason: str = ""


class KeywordCoverage(BaseModel):
    """Job-skill coverage of the tailored document.

    Attributes:
        covered: Job skills present in the document.
        missing_evidenced: Job skills missing from the document that the
            truth base does evidence — safe to ask the Tailor to surface.
        missing_unevidenced: Job skills missing that the truth base does
            not evidence — reported, never requested.
    """

    covered: list[str] = Field(default_factory=list)
    missing_evidenced: list[str] = Field(default_factory=list)
    missing_unevidenced: list[str] = Field(default_factory=list)


class TailoredDocument(BaseModel):
    """The assembled tailored CV, ready for review and later rendering.

    Attributes:
        target_title: `gold.dim_job.title_for_display`, injected.
        headline: Always equal to `target_title`.
        identity: Full name, from the truth base.
        email: Contact email, from the truth base.
        phone: Contact phone, from the truth base.
        linkedin_url: LinkedIn URL, from the truth base.
        nationality: Nationality, from the truth base.
        work_auth: Work authorisation, from the truth base.
        locations: Locations, from the truth base.
        summary: The tailored summary, if any.
        experience: Every role, in truth-base order.
        skills: The skills to show, chosen from the truth base.
        projects: Copied verbatim from the truth base.
        publications: Copied verbatim from the truth base.
        education: Copied verbatim from the truth base.
        qualifications: Copied verbatim from the truth base.
        activities: Copied verbatim from the truth base.
        stretch: The critic's seniority/scope judgement.
        keyword_coverage: Job-skill coverage.
    """

    target_title: str
    headline: str
    identity: str
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    nationality: str | None = None
    work_auth: str | None = None
    locations: list[str] = Field(default_factory=list)
    summary: TailoredSummary | None = None
    experience: list[TailoredExperience] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    qualifications: list[Certification] = Field(default_factory=list)
    activities: list[Activity] = Field(default_factory=list)
    stretch: StretchAssessment = Field(default_factory=StretchAssessment)
    keyword_coverage: KeywordCoverage = Field(default_factory=KeywordCoverage)


class TailorBullet(BaseModel):
    """One bullet instruction from the Tailor.

    Attributes:
        text: The (possibly reworded) bullet.
        evidence_refs: `bullet_id`s it is based on.
    """

    text: str
    evidence_refs: list[str] = Field(default_factory=list)


class TailorSummary(BaseModel):
    """The Tailor's proposed summary.

    Attributes:
        text: The summary text.
        evidence_refs: `bullet_id`s that support it.
    """

    text: str
    evidence_refs: list[str] = Field(default_factory=list)


class TailorExperience(BaseModel):
    """The Tailor's bullets for one role.

    Attributes:
        truth_index: Which truth-base role these bullets belong to.
        bullets: The bullets, in the order they should appear.
    """

    truth_index: int
    bullets: list[TailorBullet] = Field(default_factory=list)


class TailorOutput(BaseModel):
    """Everything the Tailor LLM returns.

    Attributes:
        summary: Proposed summary, if any.
        experience: Per-role bullet instructions.
        skills: Skill names to show, in order.
    """

    summary: TailorSummary | None = None
    experience: list[TailorExperience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class TailorOutputError(ValueError):
    """The Tailor's reply could not be parsed into a `TailorOutput`.

    Attributes:
        spent: `(model, input_tokens, output_tokens)` of the call whose reply
            was unusable, set by `run_tailor` so the run still accounts for
            the tokens; None when unknown.
    """

    spent: tuple[str, int, int] | None = None


def parse_tailor_output(text: str) -> TailorOutput:
    """Parse the Tailor LLM's reply.

    Args:
        text: The raw reply text.

    Returns:
        The validated `TailorOutput`.

    Raises:
        TailorOutputError: If the reply is not JSON, or not the expected
            shape.
    """
    try:
        data = parse_json_response(text.strip())
        return TailorOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise TailorOutputError(f"unusable Tailor reply: {exc}") from exc


@dataclass(frozen=True)
class JobSkill:
    """One skill a job asks for.

    Attributes:
        skill_id: The canonical skill id (ESCO or custom).
        label: Human-readable skill name.
        requirement_level: `must_have`, `nice_to_have` or None.
    """

    skill_id: str
    label: str
    requirement_level: str | None


@dataclass(frozen=True)
class JobContext:
    """What the Tailor and critic need to know about the target job.

    Attributes:
        job_group_id: The job's id.
        title_for_display: The title to mirror; None/blank means the job
            cannot be tailored.
        company: The employer, if known.
        description: The job description text.
        skills: The skills the job asks for.
    """

    job_group_id: str
    title_for_display: str | None
    company: str | None
    description: str
    skills: list[JobSkill]
