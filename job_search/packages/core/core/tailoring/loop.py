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
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import Engine

from core.cv.schema import CVTruthBase
from core.cv.store import read_truth_base, read_truth_base_version
from core.llm.adapters.ollama import OllamaAdapter
from core.llm.pricing import estimate_cost_usd
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.tailoring.assemble import assemble
from core.tailoring.backends import (
    Backend,
    default_backend_id,
    resolve_backends,
    unload_model,
)
from core.tailoring.cancel import RunCancelled, abort_client, run_cancellable
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
from core.tailoring.critic import CriticError, CriticResult, critic_items, run_critic
from core.tailoring.schema import (
    JobContext,
    KeywordCoverage,
    TailoredDocument,
    TailorOutputError,
)
from core.tailoring.store import (
    OrphanDraft,
    RunAlreadyFinishedError,
    StoredRun,
    create_run,
    finish_run,
    is_cancelled,
    read_run,
    set_progress,
    set_usage,
)
from core.tailoring.tailor import TASK as TAILOR_TASK
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


class UnknownBackendError(TailoringError):
    """The requested Tailor backend does not exist."""


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
        status: `approved`, `needs_review`, `failed` or `cancelled`.
        attempts: Tailor attempts made.
    """

    run_id: uuid.UUID
    status: str
    attempts: int


def _new_ollama_client() -> httpx.Client:
    """Build the dedicated HTTP client for one run's local-model calls.

    A run owns its client so cancelling can shut its socket down (see
    `core.tailoring.cancel.abort_client`) without disturbing any other
    request.

    Returns:
        A client with the long timeout CPU inference needs.
    """
    return httpx.Client(timeout=2000.0)


def _unload_in_background(backend: Backend) -> None:
    """Unload the local model without delaying the cancel.

    Args:
        backend: The Ollama backend whose model should stop computing.
    """
    threading.Thread(
        target=unload_model, args=(backend,), name="ollama-unload", daemon=True
    ).start()


def _abort_local_call(client: httpx.Client, backend: Backend) -> None:
    """Hard-stop a local Tailor call: drop the connection, then free the CPU.

    Args:
        client: The run's dedicated client, mid-request.
        backend: The Ollama backend serving it.
    """
    abort_client(client)
    _unload_in_background(backend)


def start_tailoring(
    app_engine: Engine,
    user_id: uuid.UUID,
    job_group_id: str,
    backend: str | None = None,
    config_path: Path | None = None,
) -> uuid.UUID:
    """Validate preconditions and create a `generating` run.

    Args:
        app_engine: The app-role engine.
        user_id: Whose CV to tailor.
        job_group_id: The target job.
        backend: The Tailor backend id (`claude`, `native`, `docker`);
            None picks the default for the live config.
        config_path: Task-config override (tests).

    Returns:
        The new run's id.

    Raises:
        UnknownBackendError: If `backend` is not a known backend id.
        NoCvError: If the user has no CV truth base.
        UnknownJobError: If the job does not exist.
        NoTargetTitleError: If the job's `title_for_display` is NULL/blank.
    """
    backend_id = backend or default_backend_id(config_path)
    if backend_id not in resolve_backends(config_path):
        raise UnknownBackendError(f"unknown Tailor backend {backend_id!r}")
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
        tailor_backend=backend_id,
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
        number: Its 1-based attempt number (for progress messages).
    """

    tailor_result: TailorResult
    document: TailoredDocument
    problems: list[Problem]
    critic: CriticResult | None
    number: int


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


def _stop_check(
    app_engine: Engine, user_id: uuid.UUID, run_id: uuid.UUID
) -> Callable[[], bool]:
    """Build the "has this run been cancelled?" probe.

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: The run.

    Returns:
        A zero-argument callable returning True once the run is cancelled.
    """
    return lambda: is_cancelled(app_engine, user_id, run_id)


def _raise_if_cancelled(
    app_engine: Engine, user_id: uuid.UUID, run_id: uuid.UUID
) -> None:
    """Stop the loop at once if the run was cancelled.

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: The run.

    Raises:
        RunCancelled: If the run's status is `cancelled`.
    """
    if is_cancelled(app_engine, user_id, run_id):
        raise RunCancelled(f"run {run_id} was cancelled")


def _report(
    app_engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    attempt: int,
    max_attempts: int,
    phase: str,
    message: str,
    history: list[str],
) -> None:
    """Record live progress for the review page, best effort.

    A progress write must never fail or slow the run, so every exception is
    logged and swallowed.

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: The run.
        attempt: The Tailor attempt in progress (1-based).
        max_attempts: The most attempts this run can make.
        phase: `tailoring`, `checking`, `critic` or `saving`.
        message: What is happening, in words.
        history: One line per finished attempt (copied, not kept).
    """
    try:
        set_progress(
            app_engine,
            user_id,
            run_id,
            {
                "attempt": attempt,
                "max_attempts": max_attempts,
                "phase": phase,
                "message": message,
                "phase_started_at": datetime.now(UTC).isoformat(),
                "history": list(history),
            },
        )
    except Exception:  # noqa: BLE001 — progress is advisory, never fatal
        logger.warning("could not record progress for run %s", run_id, exc_info=True)


def _critic_message(
    document: TailoredDocument,
    truth_base: CVTruthBase,
    attempt: int,
    max_attempts: int,
) -> str:
    """Describe an imminent critic call.

    Args:
        document: The document the critic will judge.
        truth_base: The truth base.
        attempt: The attempt number.
        max_attempts: The most attempts this run can make.

    Returns:
        The progress message; falls back to a generic one if the lines
        cannot be counted.
    """
    prefix = f"Attempt {attempt} of {max_attempts}: Claude is fact-checking"
    try:
        count = len(critic_items(document, truth_base))
    except Exception:  # noqa: BLE001 — a message must never fail the run
        return f"{prefix} your CV…"
    return f"{prefix} {count} line(s)…" if count else f"{prefix} the title…"


def _attempt_line(
    attempt: int,
    *,
    final: bool,
    feedback: list[str],
    document: TailoredDocument,
    problems: list[Problem],
    critic: CriticResult | None,
) -> str:
    """Summarise a finished attempt for the progress history.

    Args:
        attempt: The attempt number.
        final: Whether it was the last attempt allowed.
        feedback: The feedback it produced (empty when nothing to fix).
        document: The attempt's document.
        problems: Its code-check problems.
        critic: Its critic result.

    Returns:
        One human line.
    """
    try:
        pending = len(_orphan_drafts(document, problems, critic))
    except Exception:  # noqa: BLE001 — a message must never fail the run
        pending = len(feedback)
    if not feedback:
        return f"Attempt {attempt}: clean"
    if final:
        if pending:
            return f"Attempt {attempt}: {pending} line(s) still need your decision"
        return f"Attempt {attempt}: finished"
    return f"Attempt {attempt}: {len(feedback)} point(s) to fix — trying again"


def _usage_total(calls: list[dict]) -> dict | None:
    """Total a run's recorded LLM calls.

    Args:
        calls: The per-call records (`task`, `model`, `input_tokens`,
            `output_tokens`, `cost_usd`).

    Returns:
        The usage dict (see `StoredRun.usage`), or None when there is no
        call. The total cost is None if any call's cost is unknown.
    """
    if not calls:
        return None
    costs = [call["cost_usd"] for call in calls]
    return {
        "calls": [dict(call) for call in calls],
        "input_tokens": sum(call["input_tokens"] for call in calls),
        "output_tokens": sum(call["output_tokens"] for call in calls),
        "cost_usd": None if None in costs else round(sum(costs), 6),
    }


def _record_call(
    app_engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    calls: list[dict],
    *,
    task: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Add one finished LLM call to the run's usage, best effort.

    A usage write must never fail or slow the run, so every exception is
    logged and swallowed (the call stays in `calls` for the final write).

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: The run.
        calls: The run's per-call records (appended to).
        task: `cv_tailoring` or `fabrication_critic`.
        provider: Who served the call (`ollama` is free).
        model: The model that answered.
        input_tokens: Prompt tokens.
        output_tokens: Completion tokens.
    """
    try:
        cost = estimate_cost_usd(provider, model, input_tokens, output_tokens)
    except Exception:  # noqa: BLE001 — a price lookup must never fail the run
        logger.warning("could not price a call of run %s", run_id, exc_info=True)
        cost = None
    calls.append(
        {
            "task": task,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
        }
    )
    try:
        set_usage(app_engine, user_id, run_id, _usage_total(calls) or {})
    except Exception:  # noqa: BLE001 — usage is advisory, never fatal
        logger.warning("could not record usage for run %s", run_id, exc_info=True)


def _judge(
    app_engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    calls: list[dict],
    *,
    document: TailoredDocument,
    truth_base: CVTruthBase,
    job: JobContext,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None,
    stop: Callable[[], bool],
) -> CriticResult:
    """Run the critic (cancellable) and record its tokens and cost.

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: The run.
        calls: The run's per-call records (appended to).
        document: The document to judge.
        truth_base: The truth base.
        job: The target job.
        adapters: LLM adapters keyed by provider.
        config_path: Task-config override (tests).
        stop: The "has this run been cancelled?" probe.

    Returns:
        The critic's result.

    Raises:
        CriticError: If the reply was unusable (its tokens are recorded).
        RunCancelled: If the run was cancelled meanwhile.
    """
    try:
        result = run_cancellable(
            lambda: run_critic(
                document, truth_base, job, adapters=adapters, config_path=config_path
            ),
            should_stop=stop,
            abort=None,
        )
    except CriticError as exc:
        if exc.spent is not None:
            model, tokens_in, tokens_out = exc.spent
            _record_call(
                app_engine,
                user_id,
                run_id,
                calls,
                task=CRITIC_TASK,
                provider="anthropic",
                model=model,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
            )
        raise
    _record_call(
        app_engine,
        user_id,
        run_id,
        calls,
        task=CRITIC_TASK,
        provider="anthropic",
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    return result


def _execute(
    app_engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None,
    max_retries: int,
    progress: list[int],
    usage: list[dict],
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
        usage: Per-call usage records, appended to as calls finish, so a
            failure can record what was spent.

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
    backends = resolve_backends(config_path)
    backend = backends.get(run.tailor_backend or default_backend_id(config_path))
    if backend is None:
        raise RuntimeError(f"unknown Tailor backend {run.tailor_backend!r}")
    client: httpx.Client | None = None
    try:
        if backend.provider == "ollama":
            client = _new_ollama_client()
            adapters = {
                **adapters,
                "ollama": OllamaAdapter(base_url=backend.base_url, client=client),
            }
        return _run_loop(
            app_engine,
            user_id,
            run_id,
            run,
            backend=backend,
            adapters=adapters,
            config_path=config_path,
            max_retries=max_retries,
            progress=progress,
            usage=usage,
            abort=(
                (lambda: _abort_local_call(client, backend))
                if client is not None
                else None
            ),
        )
    finally:
        if client is not None:
            client.close()


def _run_loop(
    app_engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    run: StoredRun,
    *,
    backend: Backend,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None,
    max_retries: int,
    progress: list[int],
    usage: list[dict],
    abort: Callable[[], None] | None,
) -> TailoringOutcome:
    """Tailor, check and judge until the run can be saved.

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: The run.
        run: The run, still `generating`.
        backend: Where the Tailor runs.
        adapters: LLM adapters keyed by provider (with this run's dedicated
            Ollama adapter when the backend is local).
        config_path: Task-config override (tests).
        max_retries: Retries after the first attempt.
        progress: One-element list updated with the Tailor attempts started.
        usage: Per-call usage records (tokens, cost), appended to after every
            Tailor and critic call.
        abort: Cuts a Tailor call short on cancel: shuts the local
            model's socket down, then unloads the model (Ollama keeps
            computing through a long prompt after a disconnect); None for
            Claude.

    Returns:
        The outcome. Raises on any failure.
    """
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
    max_attempts = max_retries + 1
    history: list[str] = []
    stop = _stop_check(app_engine, user_id, run_id)
    for attempts in range(1, max_attempts + 1):
        _raise_if_cancelled(app_engine, user_id, run_id)
        progress[0] = attempts
        final = attempts == max_retries + 1
        _report(
            app_engine,
            user_id,
            run_id,
            attempt=attempts,
            max_attempts=max_attempts,
            phase="tailoring",
            message=f"Attempt {attempts} of {max_attempts}: "
            "the Tailor is rewriting your CV…",
            history=history,
        )
        try:
            tailor_result = run_cancellable(
                lambda: run_tailor(
                    truth_base,
                    job,
                    feedback,
                    adapters=adapters,
                    config_path=config_path,
                    backend=backend,
                ),
                should_stop=stop,
                abort=abort,
            )
        except TailorOutputError as exc:
            if exc.spent is not None:
                spent_model, tokens_in, tokens_out = exc.spent
                _record_call(
                    app_engine,
                    user_id,
                    run_id,
                    usage,
                    task=TAILOR_TASK,
                    provider=backend.provider,
                    model=spent_model,
                    input_tokens=tokens_in,
                    output_tokens=tokens_out,
                )
            parse_error = exc
            history.append(
                f"Attempt {attempts}: the Tailor's reply could not be used"
                + ("" if final else " — trying again")
            )
            if final:
                break
            feedback = [f"Your previous reply could not be used: {exc}"]
            continue
        _record_call(
            app_engine,
            user_id,
            run_id,
            usage,
            task=TAILOR_TASK,
            provider=backend.provider,
            model=tailor_result.model,
            input_tokens=tailor_result.input_tokens,
            output_tokens=tailor_result.output_tokens,
        )
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
        if not problems or final:
            _raise_if_cancelled(app_engine, user_id, run_id)
            _report(
                app_engine,
                user_id,
                run_id,
                attempt=attempts,
                max_attempts=max_attempts,
                phase="critic",
                message=_critic_message(document, truth_base, attempts, max_attempts),
                history=history,
            )
        critic = (
            _judge(
                app_engine,
                user_id,
                run_id,
                usage,
                document=document,
                truth_base=truth_base,
                job=job,
                adapters=adapters,
                config_path=config_path,
                stop=stop,
            )
            if not problems or final
            else None
        )
        last = _Attempt(tailor_result, document, problems, critic, attempts)
        if _is_clean(last):
            best = last
        feedback = _feedback(problems, document, critic, coverage)
        history.append(
            _attempt_line(
                attempts,
                final=final,
                feedback=feedback,
                document=document,
                problems=problems,
                critic=critic,
            )
        )
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
        _raise_if_cancelled(app_engine, user_id, run_id)
        _report(
            app_engine,
            user_id,
            run_id,
            attempt=chosen.number,
            max_attempts=max_attempts,
            phase="critic",
            message=_critic_message(
                chosen.document, truth_base, chosen.number, max_attempts
            ),
            history=history,
        )
        critic = _judge(
            app_engine,
            user_id,
            run_id,
            usage,
            document=chosen.document,
            truth_base=truth_base,
            job=job,
            adapters=adapters,
            config_path=config_path,
            stop=stop,
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
    _raise_if_cancelled(app_engine, user_id, run_id)
    _report(
        app_engine,
        user_id,
        run_id,
        attempt=attempts,
        max_attempts=max_attempts,
        phase="saving",
        message="Saving your tailored CV…",
        history=history,
    )
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
        usage=_usage_total(usage),
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
        The outcome; `cancelled` if the run was cancelled (nothing is
        written, no further LLM call is made); `failed` (with the message
        stored on the run) when anything else went wrong, including a
        critic routed away from Anthropic.
        A run that is no longer `generating` (finished by another task) is
        never re-tailored or overwritten: its stored outcome is returned.
    """
    progress = [0]
    usage: list[dict] = []
    try:
        return _execute(
            app_engine,
            user_id,
            run_id,
            adapters=adapters,
            config_path=config_path,
            max_retries=max_retries,
            progress=progress,
            usage=usage,
        )
    except RunCancelled:
        return _cancelled_outcome(run_id, progress[0])
    except RunAlreadyFinishedError:
        return _finished_elsewhere(app_engine, user_id, run_id)
    except Exception as exc:  # noqa: BLE001 — a background run must record, not raise
        if _cancelled_now(app_engine, user_id, run_id):
            # The error is the cancelled call's own (e.g. its client was
            # closed to stop it): the run stays cancelled, never `failed`.
            logger.info("tailoring run %s cancelled (%s)", run_id, exc)
            return _cancelled_outcome(run_id, progress[0])
        # The stored message is only "Type: text"; the traceback goes to the
        # API log so a failure can be traced to the line that raised it.
        logger.exception("tailoring run %s failed", run_id)
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
                usage=_usage_total(usage),
            )
        except RunAlreadyFinishedError:
            return _finished_elsewhere(app_engine, user_id, run_id)
        return TailoringOutcome(run_id=run_id, status="failed", attempts=progress[0])


def _cancelled_outcome(run_id: uuid.UUID, attempts: int) -> TailoringOutcome:
    """Build the outcome of a cancelled run.

    Args:
        run_id: The run.
        attempts: Tailor attempts started before the cancel.

    Returns:
        A `cancelled` outcome.
    """
    logger.info("tailoring run %s was cancelled", run_id)
    return TailoringOutcome(run_id=run_id, status="cancelled", attempts=attempts)


def _cancelled_now(app_engine: Engine, user_id: uuid.UUID, run_id: uuid.UUID) -> bool:
    """Check for a cancel without ever raising.

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: The run.

    Returns:
        True iff the run is cancelled; False if it is not or the check fails.
    """
    try:
        return is_cancelled(app_engine, user_id, run_id)
    except Exception:  # noqa: BLE001 — fall back to the normal failure path
        logger.warning("could not check cancel for run %s", run_id, exc_info=True)
        return False


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
    backend: str | None = None,
) -> TailoringOutcome:
    """Start and execute a tailoring run in one call (CLI, tests).

    Args:
        app_engine: The app-role engine.
        user_id: Whose CV to tailor.
        job_group_id: The target job.
        adapters: LLM adapters keyed by provider.
        config_path: Task-config override (tests).
        max_retries: Retries after the first attempt.
        backend: The Tailor backend id; None picks the default.

    Returns:
        The outcome.

    Raises:
        TailoringError: If the run cannot start (see `start_tailoring`), or
            `CriticUnavailableError` when there is no Anthropic adapter
            (raised before any run is created or any LLM is called).
    """
    ensure_critic_available(adapters, config_path)
    run_id = start_tailoring(app_engine, user_id, job_group_id, backend, config_path)
    return execute_tailoring(
        app_engine,
        user_id,
        run_id,
        adapters=adapters,
        config_path=config_path,
        max_retries=max_retries,
    )
