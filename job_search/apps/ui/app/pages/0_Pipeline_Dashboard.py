"""Pipeline dashboard — status/control-center across the whole
workflow (docs/superpowers/specs/2026-09-30-pipeline-dashboard-design.md).
Sits alongside every other page; shows state and links out rather than
replacing any of them.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import streamlit as st

from core.settings import get_settings

# A running stage that reports progress (progress_total set, so it
# heartbeats updated_at) and whose updated_at is older than this is shown
# as possibly stalled. Stages without progress reporting only touch
# updated_at at start/finish, so no staleness signal is shown for them --
# a long single-call stage would otherwise false-positive. (A comment,
# not a bare string: Streamlit "magic" would render a bare string.)
STALLED_THRESHOLD_SECONDS = 120

_PAGES_DIR = Path(__file__).parent
_REVIEW_PAGE_FILES = {
    "Dedup_Review_Queue": "2_Dedup_Review_Queue.py",
    "Categorisation_Review": "4_Categorisation_Review.py",
    "Skill_Review": "6_Skill_Review.py",
    "Scoring_Calibration": "9_Scoring_Calibration.py",
}

st.set_page_config(page_title="Pipeline Dashboard", layout="wide")
st.title("Pipeline Dashboard")

with st.expander("User manual"):
    st.markdown(
        """
Every stage of the pipeline, in one place — automated stages (a Run
button, live progress) and human-review steps (a pending count and a
link to the page where you actually do that work).

**Staleness** is dependency-based but timestamp-only: a stage is
flagged stale when something it depends on has completed more recently
than it has. This is an approximation, not a content-aware check — a
re-run that touched zero new rows still clears a staleness flag. A
stage with a **greyed-out Run button** has never had a dependency
complete at all yet — that's a stronger state than "stale."

Only one pipeline action runs at a time, system-wide — starting a
second stage while one is already running is refused, not queued.
"""
    )

_flash = st.session_state.pop("pipeline_flash", None)
if _flash:
    st.error(_flash)

_settings = get_settings()
_base = _settings.api_base_url


def _get(path: str) -> httpx.Response:
    return httpx.get(f"{_base}{path}", timeout=10.0)


def _post(path: str, json: dict) -> httpx.Response:
    return httpx.post(f"{_base}{path}", json=json, timeout=10.0)


try:
    users_response = _get("/pipeline/users")
    users_response.raise_for_status()
    users = users_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load users: {exc}")
    users = []

if not users:
    st.warning("No users found — nothing to show per-user stage state for.")
    st.stop()

if "pipeline_user_id" not in st.session_state:
    dev_user = next(
        (u for u in users if u["display_name"].lower().startswith("dev")), users[0]
    )
    st.session_state.pipeline_user_id = dev_user["id"]

user_labels = {u["id"]: f"{u['display_name']} ({u['email']})" for u in users}
selected_user_id = st.selectbox(
    "User (for per-user stages)",
    options=list(user_labels),
    format_func=lambda uid: user_labels[uid],
    key="pipeline_user_id",
)

try:
    stages_response = _get(f"/pipeline/stages?user_id={selected_user_id}")
    stages_response.raise_for_status()
    stages = stages_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load pipeline stages: {exc}")
    st.stop()

active_response = None
for stage in stages:
    if stage["kind"] != "automated" or not stage.get("has_run_button", True):
        continue
    try:
        r = _get(f"/pipeline/stages/{stage['name']}/active")
        r.raise_for_status()
        if r.json() is not None:
            active_response = (stage["name"], r.json())
            break
    except httpx.HTTPError:
        continue

_PHASES = {
    "Ingestion & Dedup": (
        "ingest",
        "enrich-engagement-terms",
        "compute-blocking-keys",
        "compute-similarity-features",
        "compute-title-similarity-scores",
        "dedup-review",
        "cluster-jobs",
        "compute-survivorship",
    ),
    "Categorisation": ("classify-jobs", "categorisation-review"),
    "CV & Skills": (
        "load-esco",
        "embed-esco",
        "extract-job-skills",
        "map-skills",
        "llm-map-skills",
        "skill-review",
        "map-cv-skills",
    ),
    "Scoring": (
        "score-filter-jobs",
        "chunk-embed-jobs",
        "chunk-embed-cv",
        "score-similarity",
        "score-skill-coverage",
        "score-llm-rerank",
        "score-blend",
        "scoring-calibration",
    ),
    "Other": ("run-evals",),
}

# Automated entries carry "name" (the registry key, e.g. "classify-jobs");
# review entries carry "key" (the registry key) plus "name" (display name).
stages_by_name = {
    (stage["name"] if stage["kind"] == "automated" else stage["key"]): stage
    for stage in stages
}

for phase, stage_keys in _PHASES.items():
    st.subheader(phase)
    for key in stage_keys:
        stage = stages_by_name.get(key)
        if stage is None:
            continue
        col1, col2, col3 = st.columns([3, 2, 2])
        if stage["kind"] == "review":
            col1.write(f"**{stage['name']}**")
            col2.write(f"{stage['pending_count']} pending")
            page_file = _REVIEW_PAGE_FILES.get(stage["page_path"])
            if page_file is not None and (_PAGES_DIR / page_file).exists():
                try:
                    col3.page_link(f"pages/{page_file}", label="Open →")
                except Exception:  # noqa: BLE001 - never let a link abort the page
                    col3.caption(f"Open the {stage['name']} page from the sidebar")
            else:
                col3.caption(f"Open the {stage['name']} page from the sidebar")
            continue

        col1.write(
            f"**{stage['name']}**"
            + ("" if stage["has_run_button"] else " _(CLI only)_")
        )
        last = stage["last_completed_at"] or "never"
        status_note = (
            f" (last attempt: {stage['last_status']})"
            if stage["last_status"] not in (None, "completed")
            else ""
        )
        col2.caption(f"last completed: {last}{status_note}")
        if stage["last_status"] == "failed" and stage.get("last_error_message"):
            col2.caption(f"✖ last error: {stage['last_error_message']}")
        if stage["is_blocked"]:
            col2.caption("⛔ blocked — a dependency has never completed")
        elif stage["is_stale"]:
            col2.caption(f"⚠ stale — {stage['stale_because']} ran more recently")

        if not stage["has_run_button"]:
            continue
        if active_response is not None and active_response[0] == key:
            _, active = active_response
            if active["progress_total"]:
                col3.progress(
                    min(active["progress_current"] / active["progress_total"], 1.0),
                    text=f"{active['progress_current']} / {active['progress_total']}",
                )
            else:
                col3.write("⟳ running…")
            updated_at = datetime.fromisoformat(active["updated_at"])
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=UTC)
            stalled_seconds = (datetime.now(UTC) - updated_at).total_seconds()
            if active["progress_total"] and stalled_seconds > STALLED_THRESHOLD_SECONDS:
                col3.warning(
                    f"possibly stalled (no progress for "
                    f"{int(stalled_seconds // 60)}m) — "
                    "the API may have restarted. Cancel only requests a stop; "
                    "if the run is truly dead, mark it cancelled with the recovery "
                    "SQL in job_search/README.md (Operational notes)."
                )
            # Cancel is only honoured by stages that report progress; a
            # single-call stage checks for cancel once, before it starts,
            # so a Cancel button on it would be a silent no-op.
            if not active["progress_total"]:
                col3.caption("in progress — this stage can't be cancelled mid-run")
            elif col3.button("Cancel", key=f"cancel-{key}"):
                cancel_response = _post(f"/pipeline/stages/{key}/cancel", {})
                if (
                    cancel_response.status_code >= 400
                    and cancel_response.status_code != 404
                ):
                    st.session_state["pipeline_flash"] = (
                        f"Failed to cancel: {cancel_response.text}"
                    )
                st.rerun()
        else:
            disabled = active_response is not None or stage["is_blocked"]
            help_text = (
                f"pipeline busy: {active_response[0]} is running"
                if active_response is not None
                else "a dependency has never completed"
                if stage["is_blocked"]
                else None
            )
            if col3.button("Run", key=f"run-{key}", disabled=disabled, help=help_text):
                body = {"user_id": selected_user_id} if stage["per_user"] else {}
                response = _post(f"/pipeline/stages/{key}/run", body)
                if response.status_code == 409:
                    st.session_state["pipeline_flash"] = (
                        "Another pipeline run just started — try again."
                    )
                elif response.status_code >= 400:
                    st.session_state["pipeline_flash"] = (
                        f"Failed to start: {response.text}"
                    )
                st.rerun()

if active_response is not None:
    time.sleep(5)
    st.rerun()
