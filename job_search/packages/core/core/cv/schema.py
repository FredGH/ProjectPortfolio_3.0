"""The CV truth-base schema (PLAN.md Step 13) — the canonical,
structured representation of a user's CV that every later step (skill
normalisation, scoring, tailored-CV generation, the fabrication critic)
reads from instead of re-parsing a PDF.
"""

from __future__ import annotations

from pydantic import BaseModel


class Bullet(BaseModel):
    """One CV bullet point, with a stable ID.

    Attributes:
        bullet_id: Computed by `core.cv.bullet_id.compute_bullet_id` —
            never trusted from an LLM's output.
        text: The bullet's text, verbatim.
    """

    bullet_id: str
    text: str


class Experience(BaseModel):
    """One work-experience entry.

    Attributes:
        company: Employer name.
        title: Job title held.
        start: Start date, "YYYY-MM".
        end: End date, "YYYY-MM", or None for "present".
        bullets: This role's bullet points.
        tech: Technologies mentioned for this role.
        metrics: Quantified achievements mentioned for this role.
    """

    company: str
    title: str
    start: str
    end: str | None = None
    bullets: list[Bullet] = []
    tech: list[str] = []
    metrics: list[str] = []


class Skill(BaseModel):
    """One skill entry.

    Attributes:
        name: The skill's name, as it appears on the CV.
        canonical_id: The ESCO ID this skill maps to — populated by
            Step 14; always None until then.
        years: Years of experience with this skill, if statable.
        last_used: "YYYY-MM" this skill was last used, if statable.
        evidence_refs: `bullet_id`s this skill is evidenced by.
    """

    name: str
    canonical_id: str | None = None
    years: float | None = None
    last_used: str | None = None
    evidence_refs: list[str] = []


class Education(BaseModel):
    """One education entry.

    Attributes:
        institution: School/university name.
        qualification: Degree or qualification name.
        start: Start date, "YYYY" or "YYYY-MM", if statable.
        end: End date, "YYYY" or "YYYY-MM", if statable.
    """

    institution: str
    qualification: str
    start: str | None = None
    end: str | None = None


class Certification(BaseModel):
    """One professional certification.

    Attributes:
        name: Certification name.
        year: Year obtained, if statable.
    """

    name: str
    year: int | None = None


class Publication(BaseModel):
    """One publication.

    Attributes:
        citation: The publication's citation text, verbatim.
    """

    citation: str


class CVTruthBase(BaseModel):
    """The full CV truth base — one user's canonical CV representation.

    Attributes:
        identity: Full name.
        headline: Professional headline, e.g. "Senior Data Engineer".
        locations: Locations associated with this CV.
        work_auth: Work authorization statement, if present.
        skills: Every skill entry.
        experience: Every work-experience entry, in CV order.
        education: Every education entry.
        certifications: Every certification.
        publications: Every publication.
    """

    identity: str
    headline: str
    locations: list[str] = []
    work_auth: str | None = None
    skills: list[Skill] = []
    experience: list[Experience] = []
    education: list[Education] = []
    certifications: list[Certification] = []
    publications: list[Publication] = []
