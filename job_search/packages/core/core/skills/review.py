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

from core.skills.normalise import candidate_forms, normalise_skill


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
        llm_verdict: The LLM pre-review's verdict, if it was asked.
        llm_custom_label: The custom skill name it proposed, if any.
        llm_note: Its one-line reason.
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
    llm_verdict: str | None = None
    llm_custom_label: str | None = None
    llm_note: str | None = None


@dataclass(frozen=True)
class MatchItem:
    """A string the mapper matched on its own, listed for verification.

    Attributes:
        raw_norm: The normalised string.
        raw_example: One original spelling.
        skill_id: The skill it was mapped to.
        skill_label: That skill's display label.
        method: "label" (an ESCO label matched exactly), "embedding" or "llm".
        score: The cosine similarity that cleared the threshold; None for a
            label match.
        suspicious: For a label match, True when the string is not the
            skill's own name (it matched through an alternative or hidden
            label of a differently named skill). Always False for an
            embedding match.
        seen_in_cv: Whether a CV contained the string.
        jd_job_count: How many jobs' extractions contain it.
        llm_note: The LLM's reason, for an `llm` match.
    """

    raw_norm: str
    raw_example: str
    skill_id: str
    skill_label: str | None
    method: str
    score: float | None
    suspicious: bool
    seen_in_cv: bool
    jd_job_count: int
    llm_note: str | None = None


@dataclass(frozen=True)
class DecisionItem:
    """A human decision (resolved or dismissed) that can be reopened.

    Attributes:
        raw_norm: The normalised string.
        raw_example: One original spelling.
        review_status: "resolved" (mapped to `skill_id`) or "dismissed".
        skill_id: The skill it was resolved to; None if dismissed.
        skill_label: That skill's display label.
        seen_in_cv: Whether a CV contained the string.
        jd_job_count: How many jobs' extractions contain it.
    """

    raw_norm: str
    raw_example: str
    review_status: str
    skill_id: str | None
    skill_label: str | None
    seen_in_cv: bool
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


def list_unmapped(
    conn: Connection, *, query: str | None = None, limit: int = 50
) -> list[ReviewItem]:
    """List unmapped strings needing review, most-requested first.

    Args:
        conn: An open connection.
        query: Optional search text (normalised, matched as a literal
            substring of the string or of its suggested skill's label).
        limit: Maximum items.

    Returns:
        Items with status `open` or `rejected`, ordered by how many jobs
        mention them, then CV presence. Empty if a given `query`
        normalises to nothing.
    """
    where = ""
    params: dict[str, Any] = {"limit": limit}
    if query is not None:
        if not normalise_skill(query):
            return []
        where = (
            "AND (m.raw_norm LIKE :p OR "
            "lower(COALESCE(es.preferred_label, cs.canonical_label)) LIKE :p) "
        )
        params["p"] = _like_pattern(query)
    rows = conn.execute(
        text(
            f"SELECT m.raw_norm, m.raw_example, m.review_status, m.seen_in_cv, "
            f"m.candidate_skill_id, m.candidate_score, "
            f"m.llm_verdict, m.llm_custom_label, m.llm_note, "
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
            f"{where}"
            f"ORDER BY jd_job_count DESC, m.seen_in_cv DESC, m.raw_norm "
            f"LIMIT :limit"
        ),
        params,
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
            llm_verdict=r.llm_verdict,
            llm_custom_label=r.llm_custom_label,
            llm_note=r.llm_note,
        )
        for r in rows
    ]


def is_suspicious_label_match(raw_norm: str, skill_label: str | None) -> bool:
    """Decide whether an exact-label match deserves a second look.

    ESCO files many tools as hidden labels under a broad skill (`kotlin` under
    "computer programming", `numpy` under "software components libraries"), so
    an exact label hit says little. It is trustworthy when the string, or its
    head with a parenthetical qualifier dropped, is the skill's own name —
    `python` for "Python (computer programming)".

    Args:
        raw_norm: The normalised string that was matched.
        skill_label: The matched skill's display label.

    Returns:
        True if neither form of the string is a form of the label. False when
        there is no label to compare (it cannot be judged).
    """
    if not skill_label:
        return False
    return not set(candidate_forms(raw_norm)) & set(candidate_forms(skill_label))


def list_auto_matches(
    conn: Connection, *, query: str | None = None, limit: int = 50
) -> list[MatchItem]:
    """List the mapper's own matches for a person to verify.

    Args:
        conn: An open connection.
        query: Optional search text (normalised, matched as a literal
            substring of the string or of its matched skill's label).
        limit: Maximum items.

    Returns:
        Rows whose method is `embedding`, `label` or `llm` (a confirm or reject moves
        them out; curated seed aliases are not listed). Order: label matches
        that look suspicious first, then embedding matches least confident
        first, then label matches that name the skill; within a label group
        the most-used string first. Empty if a given `query` normalises to
        nothing.
    """
    where = ""
    params: dict[str, Any] = {}
    if query is not None:
        if not normalise_skill(query):
            return []
        where = (
            " AND (m.raw_norm LIKE :p OR "
            "lower(COALESCE(es.preferred_label, cs.canonical_label)) LIKE :p)"
        )
        params["p"] = _like_pattern(query)
    rows = conn.execute(
        text(
            "SELECT m.raw_norm, m.raw_example, m.skill_id, m.method, m.score, "
            "m.seen_in_cv, m.llm_note, "
            "COALESCE(es.preferred_label, cs.canonical_label) AS skill_label, "
            "COALESCE(jc.job_count, 0) AS jd_job_count "
            "FROM silver.skill_mapping AS m "
            "LEFT JOIN esco.skill AS es ON es.skill_id = m.skill_id "
            "LEFT JOIN silver.custom_skill AS cs ON cs.skill_id = m.skill_id "
            "LEFT JOIN (SELECT raw_norm, count(DISTINCT job_group_id) AS job_count "
            "FROM silver.job_skill_raw GROUP BY raw_norm) AS jc "
            "ON jc.raw_norm = m.raw_norm "
            f"WHERE m.method IN ('embedding', 'label', 'llm'){where}"
        ),
        params,
    ).all()
    items = [
        MatchItem(
            raw_norm=r.raw_norm,
            raw_example=r.raw_example,
            skill_id=r.skill_id,
            skill_label=r.skill_label,
            method=r.method,
            score=float(r.score) if r.score is not None else None,
            suspicious=r.method == "label"
            and is_suspicious_label_match(r.raw_norm, r.skill_label),
            seen_in_cv=r.seen_in_cv,
            jd_job_count=r.jd_job_count,
            llm_note=r.llm_note,
        )
        for r in rows
    ]
    items.sort(key=_match_sort_key)
    return items[:limit]


def _match_sort_key(item: MatchItem) -> tuple:
    """Order auto-matches for review (see `list_auto_matches`).

    Args:
        item: One auto-match.

    Returns:
        A sort key: group first, then least confident (embedding) or
        most used (label), then the string.
    """
    if item.method == "label" and item.suspicious:
        return (0, -item.jd_job_count, item.raw_norm)
    if item.method in ("embedding", "llm"):
        return (1, item.score, item.raw_norm)
    return (2, -item.jd_job_count, item.raw_norm)


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

_AUTO_METHODS = ("embedding", "label", "llm")
"""Mapping methods the verify tab lists: the mapper's own, unreviewed matches."""


def _require_resolvable(row: Row[Any], raw_norm: str) -> None:
    """Refuse a resolve on a mapping that already carries a decision.

    Args:
        row: The locked `skill_mapping` row.
        raw_norm: The normalised string, for the message.

    Raises:
        ReviewError: Unless the row is unmapped (`open`/`rejected`) or an
            auto-match awaiting confirmation (method `embedding`, `label` or `llm`).
            A curated seed alias is settled and refused.
    """
    if row.review_status not in _RESOLVABLE_STATUSES and row.method not in (
        _AUTO_METHODS
    ):
        raise ReviewError(
            f"{raw_norm!r} is already settled (method {row.method!r}, "
            f"review status {row.review_status!r}); only an unmapped string "
            f"or an auto-match (embedding, label or llm) can be resolved"
        )


def resolve_to_skill(conn: Connection, raw_norm: str, skill_id: str) -> None:
    """Map a string to a skill and remember it as a review alias.

    Only the two states the review UI offers a resolve for are accepted: an
    unmapped string (`open` or `rejected`) and an auto-match (embedding or
    label) being confirmed. Anything else is already settled — re-pointing it would
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


def reject_auto_match(conn: Connection, raw_norm: str) -> None:
    """Reject a wrong auto-match, returning the string to the review list.

    The rejected skill is kept as the row's candidate (a suggestion, not a
    mapping), and status `rejected` protects the row from re-mapping.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.

    Raises:
        ReviewNotFound: If the string has no mapping row.
        ReviewError: If the row was not auto-mapped by the embedding, label or llm
            stage.
    """
    row = _lock_mapping(conn, raw_norm)
    if row.method not in _AUTO_METHODS:
        raise ReviewError(
            f"{raw_norm!r} was not auto-mapped by embedding, label or llm"
        )
    conn.execute(
        text(
            "UPDATE silver.skill_mapping SET candidate_skill_id = skill_id, "
            "candidate_score = score, skill_id = NULL, score = NULL, "
            "method = 'none', review_status = 'rejected' WHERE raw_norm = :n"
        ),
        {"n": raw_norm},
    )


_REOPENABLE_STATUSES = ("resolved", "dismissed")
"""Review statuses that carry a human decision a reviewer may withdraw."""


def list_decisions(
    conn: Connection, *, query: str | None = None, limit: int = 50
) -> list[DecisionItem]:
    """List resolved and dismissed strings, so a decision can be found and reopened.

    Args:
        conn: An open connection.
        query: Optional search text (normalised, matched as a literal
            substring of the string or of its target skill's label).
        limit: Maximum items.

    Returns:
        Items with status `resolved` or `dismissed`, most-used first. Empty
        if a given `query` normalises to nothing.
    """
    where = ""
    params: dict[str, Any] = {"limit": limit}
    if query is not None:
        if not normalise_skill(query):
            return []
        where = (
            "AND (m.raw_norm LIKE :p OR "
            "lower(COALESCE(es.preferred_label, cs.canonical_label)) LIKE :p) "
        )
        params["p"] = _like_pattern(query)
    rows = conn.execute(
        text(
            "SELECT m.raw_norm, m.raw_example, m.review_status, m.skill_id, "
            "m.seen_in_cv, "
            "COALESCE(es.preferred_label, cs.canonical_label) AS skill_label, "
            f"{_JD_COUNT} AS jd_job_count "
            "FROM silver.skill_mapping AS m "
            "LEFT JOIN esco.skill AS es ON es.skill_id = m.skill_id "
            "LEFT JOIN silver.custom_skill AS cs ON cs.skill_id = m.skill_id "
            "WHERE m.review_status IN ('resolved', 'dismissed') "
            f"{where}"
            "ORDER BY jd_job_count DESC, m.seen_in_cv DESC, m.raw_norm "
            "LIMIT :limit"
        ),
        params,
    ).all()
    return [
        DecisionItem(
            raw_norm=r.raw_norm,
            raw_example=r.raw_example,
            review_status=r.review_status,
            skill_id=r.skill_id,
            skill_label=r.skill_label,
            seen_in_cv=r.seen_in_cv,
            jd_job_count=r.jd_job_count,
        )
        for r in rows
    ]


def reopen(conn: Connection, raw_norm: str) -> None:
    """Withdraw a human decision, returning the string to the review list.

    A resolved string loses its `review` alias (so the old target stops
    applying to future strings) and comes back as `rejected` with the old
    target kept as the suggestion — the review page shows that as
    "Previously rejected", and `remap-unresolved` leaves it alone. A
    dismissed string comes back as `open`. The custom skill a resolution
    created, if any, is kept: other rows may reference it.

    This changes only the mapping table. The CV truth base and the job bridge
    keep the old skill id until they are refreshed (`map-cv-skills`,
    `map-skills --remap-all-auto`, `dbt run`).

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.

    Raises:
        ReviewNotFound: If the string has no mapping row.
        ReviewError: If the row carries no human decision (unmapped, an
            auto-match, or a curated seed alias).
    """
    row = _lock_mapping(conn, raw_norm)
    if row.review_status not in _REOPENABLE_STATUSES:
        raise ReviewError(
            f"{raw_norm!r} carries no decision to reopen (method {row.method!r}, "
            f"review status {row.review_status!r}); only a resolved or dismissed "
            f"string can be reopened"
        )
    if row.review_status == "dismissed":
        conn.execute(
            text(
                "UPDATE silver.skill_mapping SET review_status = 'open' "
                "WHERE raw_norm = :n"
            ),
            {"n": raw_norm},
        )
        return
    conn.execute(
        text(
            "DELETE FROM silver.skill_alias "
            "WHERE alias_norm = :n AND source = 'review'"
        ),
        {"n": raw_norm},
    )
    conn.execute(
        text(
            "UPDATE silver.skill_mapping SET candidate_skill_id = skill_id, "
            "candidate_score = NULL, skill_id = NULL, score = NULL, "
            "method = 'none', review_status = 'rejected' WHERE raw_norm = :n"
        ),
        {"n": raw_norm},
    )
