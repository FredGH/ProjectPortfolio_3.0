"""Field-level survivorship rules for a dedup cluster (PLAN.md Step 10,
DECISIONS.md §2.7 and §5). Consumed by Step 11's dim_job build — kept
here, DB-free and dbt-free, so Step 11 only needs to call these against
whatever rows it has already fetched for one job_group_id.

Note on `apply_url`: PLAN.md and DECISIONS.md name this field
`apply_url`, but no column by that name exists anywhere in the pipeline
(silver__job_posting has `job_url` and `job_url_canonical` only — PLAN.md
Steps 5a/6 never built a separate apply-vs-listing URL distinction).
This module treats `job_url` as the intended target of every
"apply_url" survivorship rule in the spec.

Note on `ClusterMember.title_for_display` and `.first_seen_at`: neither
field is produced anywhere in the current pipeline yet. `title_for_
display` is a Step 6 deliverable (DECISIONS.md §5 — decoration-stripped
but seniority-kept, distinct from `strip_title`'s output) that was
apparently never built: no dbt column and no Python function emits it.
`first_seen_at` has no source either — nothing computes a per-source
first-ingested timestamp today. This module's logic is correct and
fully tested against synthetic `ClusterMember` instances, but Step 11
will need to build/source both fields before it can construct real ones
from actual pipeline data — treat this as a known gap, not an oversight
in this module.
"""

from __future__ import annotations

from dataclasses import dataclass

_SOURCE_RANK: dict[str, int] = {
    "greenhouse": 0,  # direct ATS
    "reed": 1,
    "adzuna": 1,
    "jooble": 2,  # aggregator
    "manual": 3,  # manual/scraped entry — least trusted apply link
}
"""PLAN.md Step 10: 'direct ATS (Greenhouse/Lever/Ashby) > Reed/Adzuna >
other aggregator > scraped/manual'. Lever/Ashby and a genuine scraped
source don't exist yet in this pipeline (only the 5 sources in
core.ingestion) — extend this table, never change its ordering
semantics, when one is added."""

_UNKNOWN_SOURCE_RANK = max(_SOURCE_RANK.values()) + 1


@dataclass(frozen=True)
class ClusterMember:
    """One source posting within a resolved dedup cluster.

    Attributes:
        source_name: e.g. 'greenhouse', 'reed'.
        job_url: The posting's listing/apply URL.
        title_for_display: Step 6's decoration-stripped, seniority-kept
            title (DECISIONS.md §5) — never strip_title's output.
        description: The posting's full description text.
        first_seen_at: When this source's copy was first ingested.
    """

    source_name: str
    job_url: str
    title_for_display: str
    description: str | None
    first_seen_at: object


def _source_rank(source_name: str) -> int:
    return _SOURCE_RANK.get(source_name, _UNKNOWN_SOURCE_RANK)


def resolve_description(members: list[ClusterMember]) -> str | None:
    """Longest non-empty description wins (DECISIONS.md §2.7).

    Args:
        members: Every source posting in one dedup cluster.

    Returns:
        The longest non-empty `description` among `members`, or None if
        every member's description is None/empty.
    """
    candidates = [m.description for m in members if m.description]
    if not candidates:
        return None
    return max(candidates, key=len)


def resolve_apply_source(members: list[ClusterMember]) -> ClusterMember:
    """Pick the member whose `job_url` wins the apply-link survivorship
    rule (DECISIONS.md §2.7: source rank, not description length).

    Args:
        members: Every source posting in one dedup cluster. Must be
            non-empty.

    Returns:
        The winning `ClusterMember` — its `job_url` is the surviving
        apply_url, and (DECISIONS.md §5) its `title_for_display` is the
        one that must be used too, since both rules pick the same
        source by construction.

    Raises:
        ValueError: If `members` is empty — raised by the underlying
            `min()` call ("min() arg is an empty sequence"), since there
            is no member to pick a winner from.
    """
    return min(members, key=lambda m: (_source_rank(m.source_name), m.source_name))


def build_sources_array(
    members: list[ClusterMember],
) -> list[dict[str, object]]:
    """Build dim_job.sources[] (PLAN.md Step 11): every source URL kept,
    regardless of which one wins survivorship.

    Args:
        members: Every source posting in one dedup cluster.

    Returns:
        One `{source_name, job_url, first_seen_at}` dict per member,
        ordered by `source_name` for a deterministic array.
    """
    return [
        {
            "source_name": m.source_name,
            "job_url": m.job_url,
            "first_seen_at": m.first_seen_at,
        }
        for m in sorted(members, key=lambda m: m.source_name)
    ]
