"""Phrase-rule extraction of engagement/IR35/rate terms from a posting's
free text (PLAN.md Step 5a).

Phrase rules only — the LLM-residual pass PLAN.md describes for postings
these rules can't resolve is a deliberately deferred follow-up (see this
plan's scope note), not built here. Every field always gets an explicit
value; `unknown`/`undetermined`/`unstated` are values, never a stand-in
for "not yet implemented."
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PERMANENT_RE = re.compile(r"\bpermanent\b", re.IGNORECASE)
_FTC_RE = re.compile(r"\b(fixed[\s-]?term contract|\bftc\b)", re.IGNORECASE)
_INTERIM_RE = re.compile(r"\binterim\b", re.IGNORECASE)
_CONTRACT_RE = re.compile(
    r"\b(contract|contractor|day rate|ir35|umbrella|paye)\b", re.IGNORECASE
)

_OUTSIDE_IR35_RE = re.compile(r"\boutside\s+(?:of\s+)?ir35\b", re.IGNORECASE)
_INSIDE_IR35_RE = re.compile(r"\binside\s+(?:of\s+)?ir35\b", re.IGNORECASE)
_BARE_IR35_RE = re.compile(r"\bir35\b", re.IGNORECASE)

_AGENCY_PAYE_RE = re.compile(r"\bagency\s+paye\b", re.IGNORECASE)
_PAYE_RE = re.compile(r"\bpaye\b", re.IGNORECASE)
_UMBRELLA_RE = re.compile(r"\bumbrella\b", re.IGNORECASE)
_LIMITED_RE = re.compile(
    r"\b(own limited company|limited company|ltd company)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class EngagementClassification:
    """The engagement-type/IR35/vehicle half of a posting's engagement terms.

    Attributes:
        engagement_type: One of permanent, contract, ftc, interim, unknown.
        ir35_status: One of inside, outside, not_applicable, undetermined,
            unknown. `not_applicable` when engagement_type is permanent
            (IR35 only applies to contract engagements). `unknown` when no
            engagement-type signal was found at all. `undetermined` when
            the posting is clearly a contract engagement but IR35 status
            specifically wasn't stated or wasn't resolvable.
        engagement_vehicle: One of umbrella, limited, paye, agency_paye,
            unknown.
    """

    engagement_type: str
    ir35_status: str
    engagement_vehicle: str


def classify_engagement(description: str | None) -> EngagementClassification:
    """Classify a posting's engagement type, IR35 status and vehicle.

    Args:
        description: The posting's free text (job spec/description). `None`
            when a source has no description for this row (e.g. some
            manual entries with failed extraction).

    Returns:
        The `EngagementClassification`, with every field explicitly set —
        never `None` for any field.
    """
    text = description or ""

    if _PERMANENT_RE.search(text):
        engagement_type = "permanent"
    elif _FTC_RE.search(text):
        engagement_type = "ftc"
    elif _INTERIM_RE.search(text):
        engagement_type = "interim"
    elif _CONTRACT_RE.search(text):
        engagement_type = "contract"
    else:
        engagement_type = "unknown"

    if engagement_type == "permanent":
        ir35_status = "not_applicable"
    elif _OUTSIDE_IR35_RE.search(text):
        ir35_status = "outside"
    elif _INSIDE_IR35_RE.search(text):
        ir35_status = "inside"
    elif _BARE_IR35_RE.search(text) or engagement_type in (
        "contract",
        "ftc",
        "interim",
    ):
        ir35_status = "undetermined"
    else:
        ir35_status = "unknown"

    if _AGENCY_PAYE_RE.search(text):
        engagement_vehicle = "agency_paye"
    elif _UMBRELLA_RE.search(text):
        engagement_vehicle = "umbrella"
    elif _LIMITED_RE.search(text):
        engagement_vehicle = "limited"
    elif _PAYE_RE.search(text):
        engagement_vehicle = "paye"
    else:
        engagement_vehicle = "unknown"

    return EngagementClassification(
        engagement_type=engagement_type,
        ir35_status=ir35_status,
        engagement_vehicle=engagement_vehicle,
    )
