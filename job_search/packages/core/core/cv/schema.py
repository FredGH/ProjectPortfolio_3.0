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
        start: Start date, "YYYY-MM", or None if the CV doesn't state
            one clearly (common for older, loosely-dated roles).
        end: End date, "YYYY-MM", or None for "present".
        bullets: This role's bullet points.
        tech: Technologies mentioned for this role.
        metrics: Quantified achievements mentioned for this role.
    """

    company: str
    title: str
    start: str | None = None
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
        grade: Classification/grade awarded (e.g. "Distinction", "First
            Class", "2:1"), if statable — kept separate from
            `qualification` rather than embedded in its text.
        qualification: Degree or qualification name, if statable.
        start: Start date, "YYYY" or "YYYY-MM", if statable.
        end: End date, "YYYY" or "YYYY-MM", if statable.
    """

    institution: str
    grade: str | None = None
    qualification: str | None = None
    start: str | None = None
    end: str | None = None


class Certification(BaseModel):
    """One professional qualification or continuous-development item —
    a formal certification or a non-certification course/programme;
    both render under the CV's single "Professional Qualifications &
    Continuous Personal Development" section.

    Attributes:
        name: Certification or course/programme name.
        year: Year obtained, if statable.
    """

    name: str
    year: int | None = None


class Publication(BaseModel):
    """One publication.

    Attributes:
        citation: The publication's citation text, verbatim.
        authors: The publication's authors, as listed in the citation.
        year: Year published, if statable.
    """

    citation: str
    authors: list[str] = []
    year: int | None = None


class Project(BaseModel):
    """One personal/side project (as distinct from paid `Experience`).

    Attributes:
        name: The project's name.
        description: What it does, verbatim from the CV.
        tech: Technologies used.
        url: A link to the project, if given.
    """

    name: str
    description: str = ""
    tech: list[str] = []
    url: str | None = None


class CVTruthBase(BaseModel):
    """The full CV truth base — one user's canonical CV representation.

    Attributes:
        identity: Full name.
        headline: Professional headline, e.g. "Senior Data Engineer".
        email: Contact email address, if present.
        phone: Contact phone number, if present.
        linkedin_url: LinkedIn profile URL, if present.
        nationality: Nationality, if stated.
        summary: Professional summary/profile paragraph, if present.
        locations: Locations associated with this CV.
        work_auth: Work authorization statement, if present.
        skills: Every skill entry.
        experience: Every work-experience entry, in CV order.
        projects: Every personal/side project.
        publications: Every publication.
        education: Every education entry.
        qualifications: Professional certifications and non-certification
            continuous-development items (short courses, programmes) —
            both render under the CV's single "Professional
            Qualifications & Continuous Personal Development" section.
        activities_interests: Activities and interests, verbatim entries.
    """

    identity: str
    headline: str
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    nationality: str | None = None
    summary: str | None = None
    locations: list[str] = []
    work_auth: str | None = None
    skills: list[Skill] = []
    experience: list[Experience] = []
    projects: list[Project] = []
    publications: list[Publication] = []
    education: list[Education] = []
    qualifications: list[Certification] = []
    activities_interests: list[str] = []
