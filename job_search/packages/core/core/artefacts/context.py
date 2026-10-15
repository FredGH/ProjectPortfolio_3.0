"""The evidence a letter or pitch may cite (PLAN.md Step 19).

Every piece of evidence has an id the writer must use to cite it. The pack is
built from the user's CV, the job posting and the *saved* site-cited company
research; generation never researches a company.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import Engine

from core.company_intel import store as intel_store
from core.company_intel.keys import company_key
from core.company_intel.schema import StoredIntel
from core.cv.schema import CVTruthBase, Experience
from core.cv.store import read_truth_base
from core.tailoring.schema import JobContext

POSTING_CHARS = 6000
SITE_FIELDS = (
    "business_model",
    "size_signals",
    "tech_stack",
    "recent_announcements",
    "notes",
)


@dataclass(frozen=True)
class Evidence:
    """One citable piece of evidence.

    Attributes:
        id: The id the writer cites, e.g. `cv:abcd…` or `site:tech_stack:0`.
        label: A short human description for pickers.
        text: The evidence text the critic compares a claim with.
    """

    id: str
    label: str
    text: str


@dataclass(frozen=True)
class EvidencePack:
    """Everything a document may cite, plus its header facts.

    Attributes:
        identity: The candidate's name.
        contact: The candidate's contact line.
        title: The exact job title (`title_for_display`).
        company: The employer's name ("" when unknown).
        items: Evidence by id.
    """

    identity: str
    contact: str
    title: str
    company: str
    items: dict[str, Evidence] = field(default_factory=dict)

    @property
    def has_site(self) -> bool:
        """Say whether any saved site fact is available.

        Returns:
            True when at least one `site:` id exists.
        """
        return any(i.startswith("site:") for i in self.items)

    def texts(self, ids: Sequence[str]) -> list[str]:
        """Return the texts of the known ids among `ids`.

        Args:
            ids: Evidence ids; unknown ones are skipped.

        Returns:
            The matching evidence texts, in order.
        """
        return [self.items[i].text for i in ids if i in self.items]


def _role_text(role: Experience) -> str:
    """Describe a role as the writer may cite it.

    Args:
        role: A truth-base `Experience`.

    Returns:
        `Title at Company (start – end)`; an open role ends `present`.
    """
    end = role.end or "present"
    return f"{role.title} at {role.company} ({role.start or '?'} – {end})"


def build_pack(
    truth_base: CVTruthBase,
    job: JobContext | None = None,
    *,
    site_facts: Sequence[tuple[str, int, str]] = (),
) -> EvidencePack:
    """Build the evidence pack for one job.

    Args:
        truth_base: The user's CV.
        job: The target job, or None for a CV-only pack (interview answers).
        site_facts: Saved site-cited facts as `(field, index, text)`.

    Returns:
        The pack: CV summary, roles and bullets, the posting and its skills
        (when there is a job), and the site facts.
    """
    items: dict[str, Evidence] = {}

    def add(evidence_id: str, label: str, text: str) -> None:
        """Add one evidence item.

        Args:
            evidence_id: The citable id.
            label: A short description.
            text: The evidence text.
        """
        if text.strip():
            items[evidence_id] = Evidence(evidence_id, label, text.strip())

    if truth_base.summary:
        add("cv:summary", "Your CV summary", truth_base.summary)
    for index, role in enumerate(truth_base.experience):
        add(f"cv:role:{index}", f"Role: {role.title}", _role_text(role))
        for bullet in role.bullets:
            add(
                f"cv:{bullet.bullet_id}",
                f"{role.company}: {bullet.text[:80]}",
                bullet.text,
            )
    if job is not None:
        add("job:posting", "The job posting", job.description[:POSTING_CHARS])
        for skill in job.skills:
            add(
                f"job:skill:{skill.label}",
                f"Skill asked for: {skill.label}",
                skill.label,
            )
    for field_name, index, text in site_facts:
        add(f"site:{field_name}:{index}", f"Company site: {text[:80]}", text)
    contact = " | ".join(
        part
        for part in (truth_base.email, truth_base.phone, truth_base.linkedin_url)
        if part
    )
    return EvidencePack(
        identity=truth_base.identity,
        contact=contact,
        title=((job.title_for_display if job else "") or "").strip(),
        company=((job.company if job else "") or "").strip(),
        items=items,
    )


def saved_intel_for(engine: Engine, job: JobContext) -> StoredIntel | None:
    """Find the saved company research for a job, without researching.

    Args:
        engine: The app-role engine.
        job: The job.

    Returns:
        The latest saved intel for the resolved end client, or for the company
        named on the posting when the job was never resolved; None when none
        is saved.
    """
    resolution = intel_store.read_resolution(engine, job.job_group_id)
    key = company_key(resolution.end_client if resolution else job.company)
    return intel_store.read_latest_intel(engine, key) if key else None


@dataclass(frozen=True)
class LoadedPack:
    """An evidence pack and the versions it was built from.

    Attributes:
        pack: The evidence pack.
        truth_base_version: The CV version used.
        intel_version: The saved intel version used, or None.
    """

    pack: EvidencePack
    truth_base_version: int
    intel_version: int | None


def load_pack(engine: Engine, user_id: uuid.UUID, job: JobContext) -> LoadedPack | None:
    """Load the evidence pack for a user and a job.

    Args:
        engine: The app-role engine.
        user_id: The user.
        job: The target job.

    Returns:
        The pack and the versions it used; None when the user has no CV.
    """
    stored = read_truth_base(engine, user_id)
    if stored is None:
        return None
    intel = saved_intel_for(engine, job)
    facts: list[tuple[str, int, str]] = []
    if intel is not None:
        for field_name in SITE_FIELDS:
            for index, fact in enumerate(getattr(intel.content, field_name)):
                facts.append((field_name, index, fact.text))
    return LoadedPack(
        pack=build_pack(stored.truth_base, job, site_facts=facts),
        truth_base_version=stored.version,
        intel_version=intel.version if intel else None,
    )
