"""LLM pre-review of unmapped skill strings.

The mapper (`core.skills.mapper`) leaves a string `open` when no alias, ESCO
label or embedding at or above 0.85 matches. This module asks Claude, for each
such string, to choose among its 5 nearest ESCO skills, say none fits, or say
it is unsure:

- a high-confidence match is applied as `method = 'llm'` with `review_status`
  NULL, so it appears in the "Auto-matches — verify" list and only becomes a
  permanent alias when a person confirms it;
- anything else leaves the row `open`, with the verdict recorded for display.

Every string the model answered gets `llm_checked_at`, so it is never sent
twice. A string the model did not answer (bad JSON, missing entry, API error)
stays unchecked and is retried by the next run. Only `open` rows are ever
written: the guard is in the UPDATE itself, so a person's decision made while
a batch is in flight wins.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, text

from core.llm import gateway
from core.llm.json_response import parse_json_response
from core.llm.prompts import load_prompt
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.skills.vector import to_pgvector

TASK = "skill_mapping"
PROMPT_VERSION = "claude.v1"
BATCH_SIZE = 20
CANDIDATES_PER_STRING = 5
MAX_REPLY_TOKENS = 4096
_NOTE_MAX_CHARS = 200

_ELIGIBLE = (
    "FROM silver.skill_mapping AS m WHERE m.review_status = 'open' "
    "AND m.llm_checked_at IS NULL "
    "AND (CAST(:raw_norms AS text[]) IS NULL OR m.raw_norm = ANY(:raw_norms))"
)
_SELECT_ELIGIBLE = text(
    "SELECT m.raw_norm, m.raw_example, "
    "(SELECT count(DISTINCT r.job_group_id) FROM silver.job_skill_raw AS r "
    "WHERE r.raw_norm = m.raw_norm) AS jd_job_count "
    f"{_ELIGIBLE} ORDER BY jd_job_count DESC, m.raw_norm LIMIT :limit"
)
_COUNT_ELIGIBLE = text(f"SELECT count(*) {_ELIGIBLE}")
_NEAREST = text(
    "SELECT e.skill_id, s.preferred_label AS label, "
    "1 - (e.embedding <=> CAST(:q AS vector)) AS score "
    "FROM esco.skill_embedding AS e JOIN esco.skill AS s USING (skill_id) "
    "WHERE e.embedding_model = :model "
    "ORDER BY e.embedding <=> CAST(:q AS vector) LIMIT :k"
)
_APPLY_MATCH = text(
    "UPDATE silver.skill_mapping SET skill_id = :skill_id, method = 'llm', "
    "score = :score, candidate_skill_id = NULL, candidate_score = NULL, "
    "review_status = NULL, llm_verdict = :verdict, llm_note = :note, "
    "llm_checked_at = now(), mapped_at = now() "
    "WHERE raw_norm = :raw_norm AND review_status = 'open' "
    "AND llm_checked_at IS NULL"
)
_RECORD_VERDICT = text(
    "UPDATE silver.skill_mapping SET llm_verdict = :verdict, "
    "llm_custom_label = :custom_label, llm_note = :note, llm_checked_at = now() "
    "WHERE raw_norm = :raw_norm AND review_status = 'open' "
    "AND llm_checked_at IS NULL"
)


@dataclass(frozen=True)
class Candidate:
    """One ESCO skill offered to the model for a string.

    Attributes:
        skill_id: The ESCO skill id.
        label: Its preferred label.
        score: Cosine similarity to the string.
    """

    skill_id: str
    label: str
    score: float


@dataclass(frozen=True)
class Verdict:
    """The model's answer for one string.

    Attributes:
        kind: "match", "no_equivalent" or "unsure".
        skill_id: The chosen candidate's id (kind "match" only).
        score: The chosen candidate's cosine (kind "match" only).
        confidence: "high" or "low" (kind "match" only).
        custom_label: Suggested custom-skill name (kind "no_equivalent").
        note: The model's one-line reason.
    """

    kind: str
    skill_id: str | None = None
    score: float | None = None
    confidence: str | None = None
    custom_label: str | None = None
    note: str | None = None

    @property
    def stored(self) -> str:
        """The value kept in `silver.skill_mapping.llm_verdict`.

        Returns:
            "match_high", "match_low", "no_equivalent" or "unsure".
        """
        if self.kind == "match":
            return f"match_{self.confidence}"
        return self.kind

    @property
    def applies(self) -> bool:
        """Whether this verdict maps the string (only a high-confidence match).

        Returns:
            True for a high-confidence match.
        """
        return self.kind == "match" and self.confidence == "high"


@dataclass(frozen=True)
class LlmMapSummary:
    """Counts from one `propose_matches` run.

    Attributes:
        checked: Strings the model answered and that were recorded.
        applied: Of those, high-confidence matches applied as `llm` mappings.
        left_open: Of those, strings left open for a person.
        failed: Strings sent but not answered / not recorded (retried later).
        input_tokens: Prompt tokens billed across all calls.
        output_tokens: Completion tokens billed across all calls.
    """

    checked: int
    applied: int
    left_open: int
    failed: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class _Item:
    raw_norm: str
    raw_example: str
    candidates: list[Candidate]


def count_eligible(engine: Engine, *, raw_norms: list[str] | None = None) -> int:
    """Count strings a run would send to the model.

    Args:
        engine: A DB engine.
        raw_norms: Restrict to these strings; `None` covers everything.

    Returns:
        The number of `open`, not-yet-checked strings.
    """
    with engine.connect() as conn:
        return conn.execute(_COUNT_ELIGIBLE, {"raw_norms": raw_norms}).scalar_one()


def build_prompt(items: list[_Item], template: str) -> str:
    """Render the numbered skill list into the prompt template.

    Args:
        items: The batch, in the order that defines the numbering (1-based).
        template: The prompt file's text, with a `{strings}` placeholder.

    Returns:
        The full prompt.
    """
    blocks = []
    for number, item in enumerate(items, start=1):
        lines = [f"{number}. {item.raw_example}"]
        lines += [
            f"   {index}) {cand.label}"
            for index, cand in enumerate(item.candidates, start=1)
        ]
        blocks.append("\n".join(lines))
    return template.format(strings="\n\n".join(blocks))


def _clean(value: object) -> str | None:
    """Normalise a free-text field from the model.

    Args:
        value: The raw JSON value.

    Returns:
        A stripped, length-capped string, or None if empty / not a string.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:_NOTE_MAX_CHARS]


def _to_verdict(entry: dict[str, object], candidates: list[Candidate]) -> Verdict:
    """Turn one reply entry into a `Verdict`, treating anything odd as unsure.

    Args:
        entry: One element of the reply's `results` list.
        candidates: The candidates offered for that string.

    Returns:
        The verdict; a pick outside the offered candidates becomes "unsure".
    """
    note = _clean(entry.get("note"))
    kind = entry.get("verdict")
    if kind == "match":
        pick = entry.get("candidate")
        if isinstance(pick, int) and not isinstance(pick, bool):
            if 1 <= pick <= len(candidates):
                chosen = candidates[pick - 1]
                confidence = "high" if entry.get("confidence") == "high" else "low"
                return Verdict(
                    "match", chosen.skill_id, chosen.score, confidence, None, note
                )
        return Verdict("unsure", note=note or "picked a candidate that was not offered")
    if kind == "no_equivalent":
        return Verdict(
            "no_equivalent", custom_label=_clean(entry.get("custom_label")), note=note
        )
    return Verdict("unsure", note=note)


def parse_verdicts(text_: str, items: list[_Item]) -> dict[int, Verdict]:
    """Parse the model's reply into verdicts keyed by 0-based batch index.

    Args:
        text_: The reply text.
        items: The batch that was sent.

    Returns:
        Verdicts for the strings the reply answered validly. An out-of-range or
        repeated `n` is ignored, so a missing string is simply absent.

    Raises:
        ValueError: If the reply is not JSON or has no `results` list
            (`json.JSONDecodeError` is a `ValueError`).
    """
    data = parse_json_response(text_.strip())
    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("reply has no 'results' list")
    verdicts: dict[int, Verdict] = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        number = entry.get("n")
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        index = number - 1
        if not 0 <= index < len(items) or index in verdicts:
            continue
        verdicts[index] = _to_verdict(entry, items[index].candidates)
    return verdicts


def _candidates(
    engine: Engine, raw_norm: str, embed: Callable[[str], list[float]], model: str
) -> list[Candidate]:
    """Fetch a string's nearest ESCO skills.

    Args:
        engine: A DB engine.
        raw_norm: The normalised string (what the mapper embeds).
        embed: Maps a string to its embedding.
        model: The embedding model in use.

    Returns:
        Up to `CANDIDATES_PER_STRING` candidates, nearest first.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            _NEAREST,
            {
                "q": to_pgvector(embed(raw_norm)),
                "model": model,
                "k": CANDIDATES_PER_STRING,
            },
        ).all()
    return [Candidate(r.skill_id, r.label, float(r.score)) for r in rows]


def propose_matches(
    engine: Engine,
    *,
    adapters: dict[str, LLMAdapter],
    embed: Callable[[str], list[float]],
    embedding_model: str,
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
    raw_norms: list[str] | None = None,
    config_path: Path | None = None,
) -> LlmMapSummary:
    """Ask the model about open, unchecked strings and record its verdicts.

    Args:
        engine: A DB engine (owner or app role; both may UPDATE skill_mapping).
        adapters: LLM adapters keyed by provider; must include the task's
            provider ("anthropic").
        embed: Maps a string to its embedding (to find its ESCO candidates).
        embedding_model: The embedding model the ESCO vectors were made with.
        limit: Send at most this many strings; `None` sends every eligible one.
        batch_size: Strings per model call.
        raw_norms: Restrict to these strings; `None` covers everything.
        config_path: Task-config override (tests).

    Returns:
        Counts and token usage. A failed batch is counted in `failed` and does
        not stop the run.
    """
    template = load_prompt(TASK, load_task_config(TASK, config_path).prompt_family, 1)
    with engine.connect() as conn:
        rows = conn.execute(
            _SELECT_ELIGIBLE,
            {"raw_norms": raw_norms, "limit": limit if limit is not None else 10**9},
        ).all()
    checked = applied = failed = in_tokens = out_tokens = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        items = [
            _Item(
                r.raw_norm,
                r.raw_example,
                _candidates(engine, r.raw_norm, embed, embedding_model),
            )
            for r in chunk
        ]
        try:
            response = gateway.complete(
                TASK,
                build_prompt(items, template),
                prompt_version=PROMPT_VERSION,
                adapters=adapters,
                config_path=config_path,
                max_tokens=MAX_REPLY_TOKENS,
            )
            verdicts = parse_verdicts(response.text, items)
        except Exception:  # noqa: BLE001 — any API/parse failure = retry later
            failed += len(items)
            continue
        in_tokens += response.input_tokens
        out_tokens += response.output_tokens
        failed += len(items) - len(verdicts)
        with engine.begin() as conn:
            for index, verdict in verdicts.items():
                item = items[index]
                if verdict.applies:
                    result = conn.execute(
                        _APPLY_MATCH,
                        {
                            "skill_id": verdict.skill_id,
                            "score": verdict.score,
                            "verdict": verdict.stored,
                            "note": verdict.note,
                            "raw_norm": item.raw_norm,
                        },
                    )
                    applied += result.rowcount
                else:
                    result = conn.execute(
                        _RECORD_VERDICT,
                        {
                            "verdict": verdict.stored,
                            "custom_label": verdict.custom_label,
                            "note": verdict.note,
                            "raw_norm": item.raw_norm,
                        },
                    )
                checked += result.rowcount
    return LlmMapSummary(
        checked=checked,
        applied=applied,
        left_open=checked - applied,
        failed=failed,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
    )
