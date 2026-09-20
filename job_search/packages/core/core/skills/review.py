"""Review-list logic for unmapped and auto-mapped skills (PLAN.md Step 14).

Everything here takes an open `Connection`; the API router wraps each call in
`engine.begin()` so a resolve is one transaction. A resolution writes a
`silver.skill_alias` row (source 'review'), so the fix is made once and applies
to every future CV and JD string that normalises the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, Row, text

from core.skills.normalise import normalise_skill


class ReviewError(ValueError):
    """Raised when a review action is not valid for the row's current state."""


class ReviewNotFound(ReviewError):
    """Raised when the named skill string has no `skill_mapping` row."""


@dataclass(frozen=True)
class ReviewItem:
    """An unmapped skill string awaiting a decision.

    Attributes:
        raw_norm: The normalised string (the review key).
        raw_example: One original spelling, for display.
        review_status: "open" (never mapped) or "rejected" (an auto-match
            the reviewer threw out — its candidate is the rejected skill,
            so the UI must not offer it back as a one-click suggestion).
        seen_in_cv: Whether a CV contained it.
        jd_job_count: How many jobs' extractions contain it.
        sample_job_group_ids: Up to three such jobs.
        candidate_skill_id: The nearest ESCO skill below the threshold, if any.
        candidate_label: That skill's display label.
        candidate_score: Its cosine similarity.
    """

    raw_norm: str
    raw_example: str
    review_status: str
    seen_in_cv: bool
    jd_job_count: int
    sample_job_group_ids: list[str]
    candidate_skill_id: str | None
    candidate_label: str | None
    candidate_score: float | None


@dataclass(frozen=True)
class MatchItem:
    """A string the embedding stage auto-mapped, listed for verification.

    Attributes:
        raw_norm: The normalised string.
        raw_example: One original spelling.
        skill_id: The skill it was mapped to.
        skill_label: That skill's display label.
        score: The cosine similarity that cleared the threshold.
        jd_job_count: How many jobs' extractions contain it.
    """

    raw_norm: str
    raw_example: str
    skill_id: str
    skill_label: str | None
    score: float
    jd_job_count: int


@dataclass(frozen=True)
class SkillOption:
    """One search result the reviewer can map a string to.

    Attributes:
        skill_id: The ESCO id or `custom:<slug>`.
        label: Display label.
        source: "esco" or "custom".
    """

    skill_id: str
    label: str
    source: str


_JD_COUNT = (
    "(SELECT count(DISTINCT r.job_group_id) FROM silver.job_skill_raw AS r "
    "WHERE r.raw_norm = m.raw_norm)"
)


def list_unmapped(conn: Connection, *, limit: int = 50) -> list[ReviewItem]:
    """List unmapped strings needing review, most-requested first.

    Args:
        conn: An open connection.
        limit: Maximum items.

    Returns:
        Items with status `open` or `rejected`, ordered by how many jobs
        mention them, then CV presence.
    """
    rows = conn.execute(
        text(
            f"SELECT m.raw_norm, m.raw_example, m.review_status, m.seen_in_cv, "
            f"m.candidate_skill_id, m.candidate_score, "
            f"COALESCE(es.preferred_label, cs.canonical_label) AS candidate_label, "
            f"{_JD_COUNT} AS jd_job_count, "
            f"ARRAY(SELECT DISTINCT r.job_group_id FROM silver.job_skill_raw AS r "
            f"WHERE r.raw_norm = m.raw_norm ORDER BY r.job_group_id LIMIT 3) "
            f"AS sample_job_group_ids "
            f"FROM silver.skill_mapping AS m "
            f"LEFT JOIN esco.skill AS es ON es.skill_id = m.candidate_skill_id "
            f"LEFT JOIN silver.custom_skill AS cs "
            f"ON cs.skill_id = m.candidate_skill_id "
            f"WHERE m.review_status IN ('open', 'rejected') "
            f"ORDER BY jd_job_count DESC, m.seen_in_cv DESC, m.raw_norm "
            f"LIMIT :limit"
        ),
        {"limit": limit},
    ).all()
    return [
        ReviewItem(
            raw_norm=r.raw_norm,
            raw_example=r.raw_example,
            review_status=r.review_status,
            seen_in_cv=r.seen_in_cv,
            jd_job_count=r.jd_job_count,
            sample_job_group_ids=list(r.sample_job_group_ids),
            candidate_skill_id=r.candidate_skill_id,
            candidate_label=r.candidate_label,
            candidate_score=(
                float(r.candidate_score) if r.candidate_score is not None else None
            ),
        )
        for r in rows
    ]


def list_embedding_matches(conn: Connection, *, limit: int = 50) -> list[MatchItem]:
    """List auto-mapped embedding matches, least confident first.

    Args:
        conn: An open connection.
        limit: Maximum items.

    Returns:
        Rows whose method is `embedding` (a confirm or reject moves them out),
        lowest cosine score first.
    """
    rows = conn.execute(
        text(
            f"SELECT m.raw_norm, m.raw_example, m.skill_id, m.score, "
            f"COALESCE(es.preferred_label, cs.canonical_label) AS skill_label, "
            f"{_JD_COUNT} AS jd_job_count "
            f"FROM silver.skill_mapping AS m "
            f"LEFT JOIN esco.skill AS es ON es.skill_id = m.skill_id "
            f"LEFT JOIN silver.custom_skill AS cs ON cs.skill_id = m.skill_id "
            f"WHERE m.method = 'embedding' "
            f"ORDER BY m.score ASC, m.raw_norm LIMIT :limit"
        ),
        {"limit": limit},
    ).all()
    return [
        MatchItem(
            raw_norm=r.raw_norm,
            raw_example=r.raw_example,
            skill_id=r.skill_id,
            skill_label=r.skill_label,
            score=float(r.score),
            jd_job_count=r.jd_job_count,
        )
        for r in rows
    ]


def _escape_like(value: str) -> str:
    """Escape the LIKE metacharacters in a literal string.

    Args:
        value: The literal text to match.

    Returns:
        `value` with backslash, ``%`` and ``_`` escaped for LIKE's default
        (backslash) escape character.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_pattern(query: str) -> str:
    """Build a literal-substring LIKE pattern from a search query.

    Args:
        query: The reviewer's search text.

    Returns:
        ``%<normalised query>%`` with LIKE wildcards escaped.
    """
    return f"%{_escape_like(normalise_skill(query))}%"


def _prefix_pattern(query: str) -> str:
    """Build a literal-prefix LIKE pattern from a search query.

    Built in Python rather than concatenated in SQL so the escaping is the
    same as `_like_pattern`'s — a query containing ``%`` or ``_`` must not
    turn into a wildcard here either.

    Args:
        query: The reviewer's search text.

    Returns:
        ``<normalised query>%`` with LIKE wildcards escaped.
    """
    return f"{_escape_like(normalise_skill(query))}%"


# Ranks a matching row: exact label first, then a label starting with the
# query, then the shortest label, then alphabetically. Booleans sort false
# before true in Postgres, so each NOT-form puts its matches first. Without
# this, a short, common query ("go", "sql") could push the skill the
# reviewer wants past the UI's 10-result window purely on alphabetical
# order.
_ESCO_ORDER = (
    "ORDER BY (NOT EXISTS (SELECT 1 FROM esco.skill_label AS x "
    "WHERE x.skill_id = s.skill_id AND x.label_norm = :exact)), "
    "(NOT EXISTS (SELECT 1 FROM esco.skill_label AS x "
    "WHERE x.skill_id = s.skill_id AND x.label_norm LIKE :prefix)), "
    "length(s.preferred_label), s.preferred_label"
)
_CUSTOM_ORDER = (
    "ORDER BY (lower(canonical_label) <> :exact), "
    "(lower(canonical_label) NOT LIKE :prefix), "
    "length(canonical_label), canonical_label"
)


def search_skills(
    conn: Connection, query: str, *, limit: int = 20
) -> list[SkillOption]:
    """Find skills whose label contains the query, best match first.

    Args:
        conn: An open connection.
        query: The search text (normalised before matching).
        limit: Maximum results.

    Returns:
        Custom skills first, then ESCO skills matching on any label
        (preferred, alt or hidden). Within each group: an exact label
        match, then a label starting with the query, then the shortest
        label, then alphabetically. Empty if the query normalises to
        nothing.
    """
    exact = normalise_skill(query)
    if not exact:
        return []
    params = {
        "p": _like_pattern(query),
        "prefix": _prefix_pattern(query),
        "exact": exact,
        "limit": limit,
    }
    custom = conn.execute(
        text(
            "SELECT skill_id, canonical_label AS label FROM silver.custom_skill "
            f"WHERE lower(canonical_label) LIKE :p {_CUSTOM_ORDER} LIMIT :limit"
        ),
        params,
    ).all()
    esco = conn.execute(
        text(
            "SELECT s.skill_id, s.preferred_label AS label FROM esco.skill AS s "
            "WHERE EXISTS (SELECT 1 FROM esco.skill_label AS l "
            f"WHERE l.skill_id = s.skill_id AND l.label_norm LIKE :p) {_ESCO_ORDER} "
            "LIMIT :limit"
        ),
        params,
    ).all()
    options = [SkillOption(r.skill_id, r.label, "custom") for r in custom]
    options += [SkillOption(r.skill_id, r.label, "esco") for r in esco]
    return options[:limit]


def _lock_mapping(conn: Connection, raw_norm: str) -> Row[Any]:
    """Fetch and row-lock one mapping.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.

    Returns:
        The locked `skill_mapping` row.

    Raises:
        ReviewNotFound: If there is no such row.
    """
    row = conn.execute(
        text("SELECT * FROM silver.skill_mapping WHERE raw_norm = :n FOR UPDATE"),
        {"n": raw_norm},
    ).one_or_none()
    if row is None:
        raise ReviewNotFound(f"unknown skill string {raw_norm!r}")
    return row


_RESOLVABLE_STATUSES = ("open", "rejected")
"""Review statuses a resolve may act on: the Unmapped tab's two states."""


def _require_resolvable(row: Row[Any], raw_norm: str) -> None:
    """Refuse a resolve on a mapping that already carries a decision.

    Args:
        row: The locked `skill_mapping` row.
        raw_norm: The normalised string, for the message.

    Raises:
        ReviewError: Unless the row is unmapped (`open`/`rejected`) or is an
            embedding auto-match awaiting confirmation.
    """
    if row.review_status not in _RESOLVABLE_STATUSES and row.method != "embedding":
        raise ReviewError(
            f"{raw_norm!r} is already settled (method {row.method!r}, "
            f"review status {row.review_status!r}); only an unmapped string "
            f"or an embedding auto-match can be resolved"
        )


def resolve_to_skill(conn: Connection, raw_norm: str, skill_id: str) -> None:
    """Map a string to a skill and remember it as a review alias.

    Only the two states the review UI offers a resolve for are accepted: an
    unmapped string (`open` or `rejected`) and an embedding auto-match being
    confirmed. Anything else is already settled — re-pointing it would
    silently move an alias every past and future string shares, and the
    alias upsert's `source = 'review'` would then shield the new target from
    the seed-alias sync.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.
        skill_id: An ESCO skill id or an existing `custom:<slug>` id.

    Raises:
        ReviewNotFound: If the string has no mapping row.
        ReviewError: If the row is already settled, or if `skill_id` is in
            neither `esco.skill` nor `silver.custom_skill`.
    """
    _require_resolvable(_lock_mapping(conn, raw_norm), raw_norm)
    exists = conn.execute(
        text(
            "SELECT 1 FROM esco.skill WHERE skill_id = :i "
            "UNION ALL SELECT 1 FROM silver.custom_skill WHERE skill_id = :i LIMIT 1"
        ),
        {"i": skill_id},
    ).first()
    if exists is None:
        raise ReviewError(f"unknown skill_id {skill_id!r}")
    conn.execute(
        text(
            "INSERT INTO silver.skill_alias (alias_norm, skill_id, source) "
            "VALUES (:n, :s, 'review') ON CONFLICT (alias_norm) DO UPDATE "
            "SET skill_id = EXCLUDED.skill_id, source = 'review'"
        ),
        {"n": raw_norm, "s": skill_id},
    )
    conn.execute(
        text(
            "UPDATE silver.skill_mapping SET skill_id = :s, method = 'alias', "
            "score = NULL, candidate_skill_id = NULL, candidate_score = NULL, "
            "review_status = 'resolved', mapped_at = now() WHERE raw_norm = :n"
        ),
        {"n": raw_norm, "s": skill_id},
    )


def resolve_to_custom(conn: Connection, raw_norm: str, canonical_label: str) -> None:
    """Create (or reuse) a custom skill and map the string to it.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.
        canonical_label: The custom skill's display name; its slug becomes
            the `custom:<slug>` id.

    Raises:
        ReviewNotFound: If the string has no mapping row.
        ReviewError: If the row is already settled (see `resolve_to_skill`)
            or the label yields an empty slug.
    """
    _require_resolvable(_lock_mapping(conn, raw_norm), raw_norm)
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        normalise_skill(canonical_label).replace("+", "plus").replace("#", "sharp"),
    ).strip("-")
    if not slug:
        raise ReviewError("custom skill label must contain letters or digits")
    skill_id = f"custom:{slug}"
    conn.execute(
        text(
            "INSERT INTO silver.custom_skill (skill_id, canonical_label) "
            "VALUES (:i, :l) ON CONFLICT (skill_id) DO NOTHING"
        ),
        {"i": skill_id, "l": canonical_label.strip()},
    )
    resolve_to_skill(conn, raw_norm, skill_id)


def dismiss(conn: Connection, raw_norm: str) -> None:
    """Dismiss an unmapped string so it is never re-queued.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.

    Raises:
        ReviewNotFound: If the string has no mapping row.
        ReviewError: If it is already mapped (only unmapped strings can be
            dismissed).
    """
    row = _lock_mapping(conn, raw_norm)
    if row.skill_id is not None:
        raise ReviewError(
            f"{raw_norm!r} is mapped; only unmapped strings can be dismissed"
        )
    conn.execute(
        text(
            "UPDATE silver.skill_mapping SET review_status = 'dismissed' "
            "WHERE raw_norm = :n"
        ),
        {"n": raw_norm},
    )


def reject_embedding_match(conn: Connection, raw_norm: str) -> None:
    """Reject a wrong auto-match, returning the string to the review list.

    The rejected skill is kept as the row's candidate (a suggestion, not a
    mapping), and status `rejected` protects the row from re-mapping.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.

    Raises:
        ReviewNotFound: If the string has no mapping row.
        ReviewError: If the row was not mapped by the embedding stage.
    """
    row = _lock_mapping(conn, raw_norm)
    if row.method != "embedding":
        raise ReviewError(f"{raw_norm!r} was not auto-mapped by embedding")
    conn.execute(
        text(
            "UPDATE silver.skill_mapping SET candidate_skill_id = skill_id, "
            "candidate_score = score, skill_id = NULL, score = NULL, "
            "method = 'none', review_status = 'rejected' WHERE raw_norm = :n"
        ),
        {"n": raw_norm},
    )
