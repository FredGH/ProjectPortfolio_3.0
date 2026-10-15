"""Generate and decide artefacts (PLAN.md Step 19). Synchronous: a spinner in the UI."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import Engine

from core.artefacts import store
from core.artefacts.context import load_pack
from core.artefacts.decisions import DecisionError, apply_decision
from core.artefacts.loop import generate
from core.artefacts.schema import CoverLetter, Kind, Pitch
from core.company_intel.llm import total_usage
from core.llm.types import LLMAdapter
from core.tailoring.schema import JobContext


class NoCvError(RuntimeError):
    """The user has no CV truth base to write from."""


class NoTargetTitleError(RuntimeError):
    """The job has no `title_for_display`, so nothing can name the role."""


def generate_artefact(
    engine: Engine,
    user_id: uuid.UUID,
    kind: Kind,
    job: JobContext,
    *,
    adapters: dict[str, LLMAdapter],
    today: date | None = None,
    config_path: Path | None = None,
) -> store.StoredArtefact:
    """Generate a letter or pitch for a job and save it.

    Saved company research is read, never created.

    Args:
        engine: The app-role engine.
        user_id: The user.
        kind: `cover_letter` or `pitch`.
        job: The target job.
        adapters: LLM adapters by provider.
        today: The date on a letter (default: today).
        config_path: Task-config override (tests).

    Returns:
        The stored artefact (approved, needing review, or failed).

    Raises:
        NoTargetTitleError: If the job has no title.
        NoCvError: If the user has no CV.
        core.artefacts.critic.CriticConfigError: If the critic is not Claude.
    """
    if not (job.title_for_display or "").strip():
        raise NoTargetTitleError("this job has no title to apply for")
    loaded = load_pack(engine, user_id, job)
    if loaded is None:
        raise NoCvError("add your CV first")
    result = generate(
        kind,
        loaded.pack,
        adapters=adapters,
        today=today or date.today(),
        config_path=config_path,
    )
    artefact_id = store.save_artefact(
        engine,
        user_id,
        kind,
        job_group_id=job.job_group_id,
        truth_base_version=loaded.truth_base_version,
        intel_version=loaded.intel_version,
        target_title=loaded.pack.title,
        status=result.status,
        document=result.document,
        attempts=result.attempts,
        writer_model=result.writer_model or None,
        writer_prompt_version=result.writer_prompt_version or None,
        critic_model=result.critic_model or None,
        critic_prompt_version=result.critic_prompt_version or None,
        usage=total_usage(result.calls),
        error_message=result.error,
        open_sentences=result.open_sentences,
    )
    stored = store.read_artefact(engine, user_id, kind, artefact_id)
    assert stored is not None
    return stored


def decide(
    engine: Engine,
    user_id: uuid.UUID,
    kind: Kind,
    open_id: uuid.UUID,
    job: JobContext,
    *,
    action: str,
    evidence_ref: str | None,
) -> store.StoredArtefact:
    """Apply the user's link / remove decision to an open sentence.

    Args:
        engine: The app-role engine.
        user_id: The user.
        kind: `cover_letter` or `pitch`.
        open_id: The open sentence's row id.
        job: The job (to rebuild the evidence a link is checked against).
        action: `link` or `remove`.
        evidence_ref: For a link, an evidence id.

    Returns:
        The updated artefact.

    Raises:
        DecisionError: If the decision cannot be applied.
        store.StaleDecisionError: If the document changed or the sentence was
            already decided.
    """
    open_sentence = store.read_open_sentence(engine, user_id, open_id)
    if open_sentence is None or open_sentence.artefact_kind != kind:
        raise DecisionError("this sentence is not available - reload")
    artefact = store.read_artefact(engine, user_id, kind, open_sentence.artefact_id)
    loaded = load_pack(engine, user_id, job)
    if artefact is None or artefact.content is None or loaded is None:
        raise DecisionError("this document is not available - reload")
    if artefact.job_group_id != job.job_group_id:
        raise DecisionError("this sentence belongs to a different job - reload")
    model = CoverLetter if kind == "cover_letter" else Pitch
    base = model.model_validate(artefact.content)
    updated = apply_decision(
        base,
        open_sentence.sentence_id,
        action=action,
        evidence_ref=evidence_ref,
        pack=loaded.pack,
    )
    store.save_decision(
        engine,
        user_id,
        open_sentence=open_sentence,
        status="linked" if action == "link" else "removed",
        evidence_ref=evidence_ref,
        document=updated,
        base_document=base,
    )
    refreshed = store.read_artefact(engine, user_id, kind, artefact.id)
    assert refreshed is not None
    return refreshed
