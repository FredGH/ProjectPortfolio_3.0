"""Models for stored company intelligence and the per-job competitive angle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MAX_COMPETITORS = 5
"""The most competitors kept per company."""

Basis = Literal["site", "model_knowledge"]
PosterKind = Literal["employer", "agency", "unknown"]
Method = Literal["employer", "llm", "manual"]
IntelStatus = Literal["ok", "no_content", "failed"]


class IntelFact(BaseModel):
    """One cited fact.

    Attributes:
        text: The fact, one or two sentences.
        source_url: The crawled page it comes from.
    """

    text: str
    source_url: str


class Competitor(BaseModel):
    """One competitor.

    Attributes:
        name: The competitor's name.
        why: Why it competes, one sentence.
        basis: `site` (the company's pages name or imply it) or
            `model_knowledge` (the model's general knowledge: unverified).
        source_url: The crawled page for a `site` competitor, else None.
    """

    name: str
    why: str = ""
    basis: Basis
    source_url: str | None = None


SNAPSHOT_KEYS = (
    "industry",
    "ranking",
    "market_share",
    "growth_5y",
    "employees",
    "satisfaction",
)
"""The snapshot's rows, in display order."""

SNAPSHOT_LABELS = {
    "industry": "Industry / sector",
    "ranking": "Position / ranking in the industry",
    "market_share": "Market share",
    "growth_5y": "Growth over the past 5 years",
    "employees": "Number of employees",
    "satisfaction": "Employee satisfaction",
}
"""The human label of each snapshot row."""

NOT_KNOWN = "not known"
"""What a snapshot row says when the model has no answer."""


class SnapshotItem(BaseModel):
    """One row of the company snapshot (the model's general knowledge).

    Attributes:
        key: One of `SNAPSHOT_KEYS`.
        value: A short answer, or `not known`.
        as_of: The year the value refers to, if the model gave one.
    """

    key: str
    value: str
    as_of: str | None = None


class IntelContent(BaseModel):
    """The structured profile of a company.

    Attributes:
        business_model: Cited facts about what the company sells and to whom.
        size_signals: Cited headcount, offices and growth wording.
        tech_stack: Cited technologies in use.
        recent_announcements: Cited recent news from the company's pages.
        notes: Anything else useful for a cover letter, still cited.
        competitors: Up to `MAX_COMPETITORS` competitors.
        snapshot: The six-row company snapshot (industry, ranking, market
            share, 5-year growth, employees, satisfaction). It is the model's
            general knowledge, never crawled, so it is always unverified;
            empty for intel stored before the snapshot existed.
    """

    business_model: list[IntelFact] = Field(default_factory=list)
    size_signals: list[IntelFact] = Field(default_factory=list)
    tech_stack: list[IntelFact] = Field(default_factory=list)
    recent_announcements: list[IntelFact] = Field(default_factory=list)
    notes: list[IntelFact] = Field(default_factory=list)
    competitors: list[Competitor] = Field(default_factory=list)
    snapshot: list[SnapshotItem] = Field(default_factory=list)

    def is_empty(self) -> bool:
        """Say whether the crawled profile holds nothing at all.

        Returns:
            True when every list is empty. The snapshot is the model's own
            knowledge, so it does not count as content.
        """
        return not any(
            getattr(self, name)
            for name in type(self).model_fields
            if name != "snapshot"
        )


@dataclass(frozen=True)
class Resolution:
    """Which company a job is really for.

    Attributes:
        job_group_id: The job.
        posted_company: The company on the posting.
        poster_kind: `employer`, `agency` or `unknown`.
        end_client: The company to research, or None when unknown.
        website_url: A candidate official website, or None.
        method: `employer`, `llm` or `manual`.
    """

    job_group_id: str
    posted_company: str | None
    poster_kind: PosterKind
    end_client: str | None
    website_url: str | None
    method: Method


@dataclass(frozen=True)
class StoredIntel:
    """One stored version of a company's intel.

    Attributes:
        company_key: The company's key.
        version: The version number (1 is the first).
        display_name: The name the intel was researched under.
        status: `ok`, `no_content` or `failed`.
        researched_at: When this version was written.
        content: The structured profile.
        pages: Each crawled page: `{url, status, error}`.
        resolved_from_job: The job that triggered the research.
        model: The extraction model.
        prompt_version: The extraction prompt version.
        usage: Token use and cost of the research.
    """

    company_key: str
    version: int
    display_name: str
    status: IntelStatus
    researched_at: datetime
    content: IntelContent
    pages: list[dict]
    resolved_from_job: str | None
    model: str | None
    prompt_version: str | None
    usage: dict


class AngleAdvantage(BaseModel):
    """One way the transformation could put the company ahead.

    Attributes:
        competitor: A listed competitor's exact name, or "" for the field.
        point: One grounded sentence.
    """

    competitor: str = ""
    point: str


class AngleContent(BaseModel):
    """The per-job competitive analysis (analysis, not fact).

    Attributes:
        transformation: What change the job implies for the company.
        advantages: How that change could put the company ahead.
        caveats: What is uncertain.
    """

    transformation: str = ""
    advantages: list[AngleAdvantage] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class StoredAngle:
    """A stored competitive angle.

    Attributes:
        job_group_id: The job it is for.
        company_key: The company it concerns.
        intel_version: The intel version it was written from.
        content: The analysis.
        model: The model that wrote it.
        prompt_version: The prompt version.
        usage: Token use and cost.
        created_at: When it was written.
    """

    job_group_id: str
    company_key: str
    intel_version: int
    content: AngleContent
    model: str | None
    prompt_version: str | None
    usage: dict
    created_at: datetime
