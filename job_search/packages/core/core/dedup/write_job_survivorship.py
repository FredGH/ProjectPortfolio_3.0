"""Batch write path for silver.job_survivorship (PLAN.md Step 11).

For every job_group_id in silver.job_identity_map, resolves the
field-level survivorship winners Step 10's core.dedup.survivorship
module decided how to compute but never had a caller for: which
source's description wins (longest non-empty), which source's
apply_url/title_for_display wins (source rank), per DECISIONS.md §2.7
and §5. Runs outside dbt for the same reason every earlier "Python
computes, dbt joins" step did — but the deeper reason here is reuse,
not SQL's inability: source-rank tie-breaking and the longest-string
rule ARE expressible in SQL, but reusing the exact, already-tested
survivorship.py functions here means Step 10's test suite IS this
step's correctness guarantee. A second SQL implementation of the same
rules would need its own tests and could silently drift from the
first.

Also computes title_for_display here, per posting, using Step 6's
core.normalisation.title.title_for_display — a pure function that
existed since Step 6 but was never wired into a persisted column until
now (see this plan's "Scope note").

Unlike silver.job_identity_map, this table is a plain UPSERT: a
survivorship winner is not an identity decision (job_group_id is the
only value PLAN.md requires to be immutable) — if new source data
arrives for an existing group, its winner should be free to change.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import Engine, text

from core.dedup.survivorship import (
    ClusterMember,
    resolve_apply_source,
    resolve_description,
)
from core.normalisation.title import title_for_display

_SELECT_GROUP_MEMBERS = text(
    """
    SELECT
        im.job_group_id,
        sp.source_name,
        sp.source_job_id,
        sp.job_url,
        sp.title,
        sp.description
    FROM silver.job_identity_map AS im
    INNER JOIN silver.silver__job_posting AS sp
        ON im.source_name = sp.source_name AND im.source_job_id = sp.source_job_id
    ORDER BY im.job_group_id, sp.source_name, sp.source_job_id
    """
)

_UPSERT = text(
    """
    INSERT INTO silver.job_survivorship (
        job_group_id, winning_description, apply_source_name,
        apply_source_job_id, apply_job_url, apply_title_for_display
    ) VALUES (
        :job_group_id, :winning_description, :apply_source_name,
        :apply_source_job_id, :apply_job_url, :apply_title_for_display
    )
    ON CONFLICT (job_group_id) DO UPDATE SET
        winning_description = EXCLUDED.winning_description,
        apply_source_name = EXCLUDED.apply_source_name,
        apply_source_job_id = EXCLUDED.apply_source_job_id,
        apply_job_url = EXCLUDED.apply_job_url,
        apply_title_for_display = EXCLUDED.apply_title_for_display,
        computed_at = now()
    """
)


def write_job_survivorship(engine: Engine) -> int:
    """Resolve and upsert one survivorship row per job_group_id.

    Args:
        engine: The migration/owner engine — SHARED-zone, like every
            other dedup/silver Python-written table.

    Returns:
        The number of job_group_id groups written (inserted or
        updated).
    """
    with engine.begin() as conn:
        rows = conn.execute(_SELECT_GROUP_MEMBERS).all()

        members_by_group: dict[str, list[ClusterMember]] = defaultdict(list)
        # ClusterMember has no source_job_id field (Step 10 never
        # needed one for pure resolution) — track it separately, keyed
        # by (source_name, job_url), to recover the winner's natural
        # key afterwards. Assumes job_url is unique per source within a
        # group, true for any real posting.
        source_job_id_by_group: dict[str, dict[tuple[str, str], str]] = defaultdict(
            dict
        )
        for row in rows:
            display_title = title_for_display(row.title)
            members_by_group[row.job_group_id].append(
                ClusterMember(
                    source_name=row.source_name,
                    job_url=row.job_url,
                    title_for_display=display_title or "",
                    description=row.description,
                    first_seen_at=None,
                )
            )
            # First-wins, matching resolve_apply_source's min() semantics:
            # when two postings in a group share both source_name and
            # job_url (the same-source-repost scenario), the SELECT's
            # ORDER BY guarantees the lowest source_job_id arrives first,
            # so setdefault keeps it — the same member min() will pick.
            source_job_id_by_group[row.job_group_id].setdefault(
                (row.source_name, row.job_url), row.source_job_id
            )

        written = 0
        for job_group_id, members in members_by_group.items():
            winning_description = resolve_description(members)
            winner = resolve_apply_source(members)
            winner_source_job_id = source_job_id_by_group[job_group_id][
                (winner.source_name, winner.job_url)
            ]
            conn.execute(
                _UPSERT,
                {
                    "job_group_id": job_group_id,
                    "winning_description": winning_description,
                    "apply_source_name": winner.source_name,
                    "apply_source_job_id": winner_source_job_id,
                    "apply_job_url": winner.job_url,
                    "apply_title_for_display": winner.title_for_display or None,
                },
            )
            written += 1
    return written
