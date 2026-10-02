"""The tailoring loop: tailor → assemble → check → critic, retried at most
twice, then persisted (Step 17).

`start_tailoring` validates and creates the run synchronously so the API
can answer immediately; `execute_tailoring` does the slow part (it is what
runs as a background task) and never raises — every failure ends the run
`failed` with a message, and no unchecked line is ever approved.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from core.cv.store import read_truth_base, read_truth_base_version
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.tailoring.assemble import assemble
from core.tailoring.checks import (
    STRUCTURAL_CODES,
    Problem,
    check_evidence_refs,
    check_experience_unchanged,
    check_headline,
    compute_keyword_coverage,
    line_location,
)
from core.tailoring.context import load_job_context
from core.tailoring.critic import TASK as CRITIC_TASK
from core.tailoring.critic import CriticResult, run_critic
from core.tailoring.schema import (
    JobContext,
    KeywordCoverage,
    TailoredDocument,
    TailorOutputError,
)
from core.tailoring.store import (
    OrphanDraft,
    RunAlreadyFinishedError,
    create_run,
    finish_run,
    read_run,
)
from core.tailoring.tailor import TailorResult, run_tailor

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
"""Tailor retries after the first attempt (PLAN.md Step 17: at most twice)."""

_ITEM_ID_RE = re.compile(r"^e(\d+)b(\d+)$")


class TailoringError(Exception):
    """A tailoring run cannot start."""


class NoCvError(TailoringError):
    """The user has no CV truth base."""


class UnknownJobError(TailoringError):
    """No job with that `job_group_id` exists."""


class NoTargetTitleError(TailoringError):
    """The job has no `title_for_display`, so there is nothing to mirror."""


class CriticUnavailableError(TailoringError):
    """No adapter exists for the critic's provider (no Anthropic API key)."""


def ensure_critic_available(
    adapters: dict[str, LLMAdapter], config_path: Path | None = None
) -> None:
    """Refuse to start unless the fabrication critic can actually be called.

    The critic always runs on Claude; without an Anthropic adapter every
    run would only fail after the (slow) Tailor call.

    Args:
        adapters: LLM adapters keyed by provider.
        config_path: Task-config override (tests).

    Raises:
        CriticUnavailableError: If `adapters` has no adapter for the
            `fabrication_critic` task's provider.
    """
    provider = load_task_config(CRITIC_TASK, config_path).provider
    if provider not in adapters:
        raise CriticUnavailableError(
            "the fabrication critic needs an Anthropic API key (set ANTHROPIC_API_KEY)"
        )


@dataclass(frozen=True)
class TailoringOutcome:
    """How a run ended.

    Attributes:
        run_id: The run.
        status: `approved`, `needs_review` or `failed`.
        attempts: Tailor attempts made.
    """

    run_id: uuid.UUID
    status: str
    attempts: int


def start_tailoring(
    app_engine: Engine, user_id: uuid.UUID, job_group_id: str
) -> uuid.UUID:
    """Validate preconditions and create a `generating` run.

    Args:
        app_engine: The app-role engine.
        user_id: Whose CV to tailor.
        job_group_id: The target job.

    Returns:
        The new run's id.

    Raises:
        NoCvError: If the user has no CV truth base.
        UnknownJobError: If the job does not exist.
        NoTargetTitleError: If the job's `title_for_display` is NULL/blank.
    """
    stored = read_truth_base(app_engine, user_id)
    if stored is None:
        raise NoCvError("this user has no CV truth base yet")
    job = load_job_context(app_engine, job_group_id)
    if job is None:
        raise UnknownJobError(f"no job {job_group_id!r}")
    if not (job.title_for_display or "").strip():
        raise NoTargetTitleError(
            f"job {job_group_id!r} has no title_for_display, so there is no "
            "title to mirror"
        )
    return create_run(
        app_engine,
        user_id,
        job_group_id=job_group_id,
        truth_base_version=stored.version,
        target_title=job.title_for_display.strip(),
    )


def _feedback(
    problems: list[Problem],
    document: TailoredDocument,
    critic: CriticResult | None,
    coverage: KeywordCoverage,
) -> list[str]:
    """Build the concrete feedback for the Tailor's next attempt.

    Args:
        problems: Code-check problems from this attempt.
        document: This attempt's document.
        critic: This attempt's critic result, if the critic ran.
        coverage: This attempt's keyword coverage.

    Returns:
        Messages; empty when nothing needs fixing.
    """
    messages = [problem.message for problem in problems]
    for role in document.experience:
        for position, bullet in enumerate(role.bullets):
            if bullet.origin == "orphan":
                messages.append(
                    f"bullet e{role.truth_index}b{position} ({bullet.text!r}) cites no "
                    "valid bullet id — cite the bullet it is based on, or remove it"
                )
    if document.summary is not None and document.summary.origin == "orphan":
        messages.append(
            "the summary cites no valid bullet id — cite the bullets it is based "
            "on, or reuse the original summary"
        )
    if critic is not None:
        for item_id, verdict in critic.verdicts.items():
            if not verdict.supported:
                messages.append(
                    f"line {item_id} was rejected as unsupported: {verdict.issue}. "
                    "Use only what its cited bullets state"
                )
    if coverage.missing_evidenced:
        messages.append(
            "surface these skills the candidate genuinely has: "
            + ", ".join(coverage.missing_evidenced)
        )
    return messages


def _draft(
    document: TailoredDocument, location: str, kind: str, issue: str | None
) -> OrphanDraft:
    """Build an orphan draft for a line.

    Args:
        document: The final document.
        location: `summary` or `e{role}b{bullet}`.
        kind: `orphan` or `unsupported`.
        issue: What is wrong.

    Returns:
        The draft.

    Raises:
        RuntimeError: If the location does not resolve to a line. A problem
            that cannot be shown must fail the run, never be dropped.
    """
    if location == "summary":
        if document.summary is None:
            raise RuntimeError("cannot surface a problem on a missing summary")
        return OrphanDraft(
            kind=kind,
            section="summary",
            experience_index=None,
            bullet_index=None,
            text=document.summary.text,
            claimed_refs=list(document.summary.evidence_refs),
            issue=issue,
        )
    match = _ITEM_ID_RE.match(location)
    if match is None:
        raise RuntimeError(f"cannot surface a problem at location {location!r}")
    role, position = int(match.group(1)), int(match.group(2))
    if role >= len(document.experience) or position >= len(
        document.experience[role].bullets
    ):
        raise RuntimeError(f"cannot surface a problem at location {location!r}")
    bullet = document.experience[role].bullets[position]
    return OrphanDraft(
        kind=kind,
        section="experience",
        experience_index=role,
        bullet_index=position,
        text=bullet.text,
        claimed_refs=list(bullet.evidence_refs),
        issue=issue,
    )


def _orphan_drafts(
    document: TailoredDocument,
    problems: list[Problem],
    critic: CriticResult | None,
) -> list[OrphanDraft]:
    """Turn every unresolved line into an orphan row.

    One row per line. A critic rejection wins over an evidence problem
    (the line has a valid source but claims too much); an evidence problem
    or missing source is an `orphan`.

    Args:
        document: The final document.
        problems: The final attempt's code-check problems.
        critic: The final attempt's critic result.

    Returns:
        Drafts in document order (summary first).
    """
    issues: dict[str, tuple[str, str | None]] = {}
    if document.summary is not None and document.summary.origin == "orphan":
        issues["summary"] = ("orphan", "no evidence_ref in the CV")
    for role in document.experience:
        for position, bullet in enumerate(role.bullets):
            if bullet.origin == "orphan":
                issues[f"e{role.truth_index}b{position}"] = (
                    "orphan",
                    "no evidence_ref in the CV",
                )
    for problem in problems:
        if problem.code not in STRUCTURAL_CODES:
            issues[problem.location] = ("orphan", problem.message)
    if critic is not None:
        for item_id, verdict in critic.verdicts.items():
            if not verdict.supported:
                issues[item_id] = ("unsupported", verdict.issue)

    drafts = [
        _draft(document, location, kind, issue)
        for location, (kind, issue) in issues.items()
    ]
    return sorted(
        drafts,
        key=lambda d: (
            d.section != "summary",
            d.experience_index or 0,
            d.bullet_index or 0,
        ),
    )


@dataclass(frozen=True)
class _Attempt:
    """One Tailor attempt that assembled a document.

    Attributes:
        tailor_result: The Tailor call's result.
        document: The assembled document (with keyword coverage).
        problems: Its code-check problems.
        critic: Its critic result, or None when the critic was skipped.
    """

    tailor_result: TailorResult
    document: TailoredDocument
    problems: list[Problem]
    critic: CriticResult | None


def _is_clean(attempt: _Attempt) -> bool:
    """Whether an attempt can be approved as it stands.

    Args:
        attempt: The attempt.

    Returns:
        True when it has no code problems, was judged by the critic, and
        leaves no orphan or unsupported line. Keyword gaps do not count.
    """
    return (
        not attempt.problems
        and attempt.critic is not None
        and not _orphan_drafts(attempt.document, attempt.problems, attempt.critic)
    )


def _execute(
    app_engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None,
    max_retries: int,
    progress: list[int],
) -> TailoringOutcome:
    """Run the loop for an existing `generating` run.

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: The run.
        adapters: LLM adapters keyed by provider.
        config_path: Task-config override (tests).
        max_retries: Retries after the first attempt.
        progress: One-element list updated with the number of Tailor attempts
            started, so a failure can record the real count.

    Returns:
        The outcome. Raises on any failure — `execute_tailoring` turns that
        into a `failed` run.
    """
    ensure_critic_available(adapters, config_path)
    run = read_run(app_engine, user_id, run_id)
    if run is None:
        raise RuntimeError(f"run {run_id} not found")
    if run.status != "generating":
        # Already finished (a duplicate or late task): never re-tailor.
        return TailoringOutcome(run_id=run_id, status=run.status, attempts=run.attempts)
    stored = read_truth_base_version(app_engine, user_id, run.truth_base_version)
    if stored is None:
        raise RuntimeError(f"CV version {run.truth_base_version} no longer exists")
    truth_base = stored.truth_base
    job: JobContext | None = load_job_context(app_engine, run.job_group_id)
    if job is None:
        raise RuntimeError(f"job {run.job_group_id!r} no longer exists")

    feedback: list[str] = []
    best: _Attempt | None = None
    last: _Attempt | None = None
    parse_error: TailorOutputError | None = None
    attempts = 0
    for attempts in range(1, max_retries + 2):
        progress[0] = attempts
        final = attempts == max_retries + 1
        try:
            tailor_result = run_tailor(
                truth_base, job, feedback, adapters=adapters, config_path=config_path
            )
        except TailorOutputError as exc:
            parse_error = exc
            if final:
                break
            feedback = [f"Your previous reply could not be used: {exc}"]
            continue
        document = assemble(
            truth_base, tailor_result.output, target_title=run.target_title
        )
        problems = (
            check_evidence_refs(document, truth_base)
            + check_experience_unchanged(document, truth_base)
            + check_headline(document)
        )
        coverage = compute_keyword_coverage(document, truth_base, job.skills)
        document = document.model_copy(update={"keyword_coverage": coverage})
        critic = (
            run_critic(
                document, truth_base, job, adapters=adapters, config_path=config_path
            )
            if not problems or final
            else None
        )
        last = _Attempt(tailor_result, document, problems, critic)
        if _is_clean(last):
            best = last
        feedback = _feedback(problems, document, critic, coverage)
        if not feedback or final:
            break

    # A clean attempt is never replaced by a later, worse one (or by a
    # reply that could not be parsed); otherwise the last usable attempt.
    chosen = best or last
    if chosen is None:
        assert parse_error is not None
        raise parse_error
    structural = [p for p in chosen.problems if p.code in STRUCTURAL_CODES]
    if structural:
        raise RuntimeError(
            "the document failed a structural check: "
            + "; ".join(p.message for p in structural)
        )
    critic = chosen.critic
    if critic is None:
        # The critic was skipped on this attempt (it had code problems) and
        # no later attempt was usable: judge it now — an unjudged reworded
        # line must never be persisted as approved.
        critic = run_critic(
            chosen.document, truth_base, job, adapters=adapters, config_path=config_path
        )
    document = chosen.document.model_copy(update={"stretch": critic.stretch})
    orphans = _orphan_drafts(document, chosen.problems, critic)
    # Persisted coverage counts only traced lines: never a line awaiting a
    # decision (orphan or unsupported).
    pending = frozenset(
        line_location(d.section, d.experience_index, d.bullet_index) for d in orphans
    )
    coverage = compute_keyword_coverage(
        document, truth_base, job.skills, exclude=pending
    )
    document = document.model_copy(update={"keyword_coverage": coverage})
    status = "needs_review" if orphans else "approved"
    finish_run(
        app_engine,
        user_id,
        run_id,
        status=status,
        document=document,
        orphans=orphans,
        attempts=attempts,
        tailor_model=chosen.tailor_result.model,
        tailor_prompt_version=chosen.tailor_result.prompt_version,
        critic_model=critic.model,
        critic_prompt_version=critic.prompt_version,
    )
    return TailoringOutcome(run_id=run_id, status=status, attempts=attempts)


def execute_tailoring(
    app_engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
    max_retries: int = MAX_RETRIES,
) -> TailoringOutcome:
    """Run the loop for a started run. Never raises.

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: A run created by `start_tailoring`.
        adapters: LLM adapters keyed by provider.
        config_path: Task-config override (tests).
        max_retries: Retries after the first attempt.

    Returns:
        The outcome; `failed` (with the message stored on the run) when
        anything went wrong, including a critic routed away from Anthropic.
        A run that is no longer `generating` (finished by another task) is
        never re-tailored or overwritten: its stored outcome is returned.
    """
    progress = [0]
    try:
        return _execute(
            app_engine,
            user_id,
            run_id,
            adapters=adapters,
            config_path=config_path,
            max_retries=max_retries,
            progress=progress,
        )
    except RunAlreadyFinishedError:
        return _finished_elsewhere(app_engine, user_id, run_id)
    except Exception as exc:  # noqa: BLE001 — a background run must record, not raise
        message = f"{type(exc).__name__}: {exc}"[:500]
        try:
            finish_run(
                app_engine,
                user_id,
                run_id,
                status="failed",
                document=None,
                orphans=[],
                attempts=progress[0],
                error_message=message,
            )
        except RunAlreadyFinishedError:
            return _finished_elsewhere(app_engine, user_id, run_id)
        return TailoringOutcome(run_id=run_id, status="failed", attempts=progress[0])


def _finished_elsewhere(
    app_engine: Engine, user_id: uuid.UUID, run_id: uuid.UUID
) -> TailoringOutcome:
    """Report a run that something else finished first, without touching it.

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: The run.

    Returns:
        The stored outcome; `failed` with 0 attempts if the run is not
        visible at all.
    """
    logger.warning("tailoring run %s was already finished; not overwritten", run_id)
    run = read_run(app_engine, user_id, run_id)
    if run is None:
        return TailoringOutcome(run_id=run_id, status="failed", attempts=0)
    return TailoringOutcome(run_id=run_id, status=run.status, attempts=run.attempts)


def run_tailoring(
    app_engine: Engine,
    user_id: uuid.UUID,
    job_group_id: str,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
    max_retries: int = MAX_RETRIES,
) -> TailoringOutcome:
    """Start and execute a tailoring run in one call (CLI, tests).

    Args:
        app_engine: The app-role engine.
        user_id: Whose CV to tailor.
        job_group_id: The target job.
        adapters: LLM adapters keyed by provider.
        config_path: Task-config override (tests).
        max_retries: Retries after the first attempt.

    Returns:
        The outcome.

    Raises:
        TailoringError: If the run cannot start (see `start_tailoring`), or
            `CriticUnavailableError` when there is no Anthropic adapter
            (raised before any run is created or any LLM is called).
    """
    ensure_critic_available(adapters, config_path)
    run_id = start_tailoring(app_engine, user_id, job_group_id)
    return execute_tailoring(
        app_engine,
        user_id,
        run_id,
        adapters=adapters,
        config_path=config_path,
        max_retries=max_retries,
    )
