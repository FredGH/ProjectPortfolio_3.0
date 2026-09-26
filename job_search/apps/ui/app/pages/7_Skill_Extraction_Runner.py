"""Skill-extraction batch runner (Step 14 follow-up) — trigger a scoped
extraction run from the UI instead of a shell command, safe on both
native and Docker-hosted Ollama. See docs/superpowers/specs/
2026-09-23-skill-extraction-batch-runner-design.md.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Skill Extraction Runner", layout="wide")
st.title("Skill Extraction Runner")

_API = get_settings().api_base_url
# 30 minutes. The run bumps `updated_at` after every job (about 35-150s each;
# a job whose chunk hits the token cap and is retried can take several
# minutes), so half an hour of silence means the process running it is gone,
# not that it is between batches. Kept generous on purpose: the advertised
# recovery (a manual UPDATE clearing the row) would let a second run start
# alongside a still-running first one if it were used on a healthy run.
# See README.md's "Running a batch from the UI" section.
_STALE_AFTER_SECONDS = 1800

# The dbt refresh that makes newly mapped skills show up in the job-skill
# bridge. dbt cannot run inside the API image, so the page can only show it.
_DBT_REFRESH_COMMAND = (
    "cd dbt && dbt run --select silver__skill silver__bridge_job_skill"
)

# "docker" is first so it's the selectbox's default — it always works
# whenever the stack is up, unlike "native", which needs Ollama actually
# running on the host with the model pulled.
_OLLAMA_LOCATION_LABELS = {
    "docker": "Docker (this stack's ollama service)",
    "native": "Native (Ollama on your host machine)",
}

_USER_GUIDE = """
Runs `extract-job-skills` for the sources/countries you pick, in
bounded batches of 30 jobs with a pause between each — the same
protection `scripts/extract_in_batches.sh` uses, so it's safe to run
even though it can take a long time (a local model on CPU takes
minutes per job).

**Ollama location** picks where those calls go: **Docker** (this
stack's `ollama` service, always available) or **Native** (Ollama
running on your host machine — faster, per README.md, but only works
if it's actually running there with the model pulled; if it isn't, the
run fails on its first sub-batch).

Progress updates after **every job**. Only one run can be active at a
time. **Stop** takes effect as soon as the job currently being
extracted finishes (typically a minute or two) — nothing already
extracted is undone, and a stopped run can always be restarted later to
pick up where it left off.

When a run **completes**, the skills it extracted are **mapped to ESCO
automatically** and the result is shown here (mapped vs. needing
review). A stopped or failed run skips this; it happens after the next
completed run. The job–skill **bridge** is a dbt model and is *not*
refreshed automatically (dbt can't run inside the API): from
`job_search/`, run `cd dbt && dbt run --select silver__skill
silver__bridge_job_skill`.

If a run shows as **possibly stalled**, the API process running it was
restarted (its progress hasn't moved in over half an hour). The jobs it
already extracted are safe. **Known limitation:** Stop only asks a live
run to stop — if the process itself is gone (e.g. after an API
restart), nothing is left to act on that request, so the row stays
stuck. Clearing it currently needs a manual database update, e.g.:
`UPDATE silver.skill_extraction_run SET status = 'cancelled',
finished_at = now(), updated_at = now() WHERE run_id = '<id>';` —
before a new run can start.
"""


def _get(path: str, params: dict | None = None) -> dict:
    """GET a JSON endpoint on the API.

    Args:
        path: The endpoint path.
        params: Query parameters.

    Returns:
        The parsed JSON body.

    Raises:
        httpx.HTTPError: If the request fails.
    """
    response = httpx.get(f"{_API}{path}", params=params, timeout=10.0)
    response.raise_for_status()
    return response.json()


def _post(path: str, payload: dict | None = None) -> httpx.Response:
    """POST an action to the API.

    Args:
        path: The endpoint path.
        payload: The JSON body, if any.

    Returns:
        The raw response (the caller decides how to interpret status).
    """
    return httpx.post(f"{_API}{path}", json=payload, timeout=10.0)


def _error_message(exc: httpx.HTTPStatusError) -> str:
    """Build a readable message from an HTTP error response.

    Args:
        exc: The status error raised for the response.

    Returns:
        The API's `detail`, or the exception text if there isn't one.
    """
    try:
        detail = exc.response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else str(exc)


def _is_stale(updated_at: str) -> bool:
    """Check whether a run's progress hasn't moved recently.

    Args:
        updated_at: The run's `updated_at` timestamp, ISO 8601.

    Returns:
        True if more than `_STALE_AFTER_SECONDS` have passed since
        `updated_at` — most likely because the API process restarted
        mid-run.
    """
    last_update = datetime.fromisoformat(updated_at)
    age = datetime.now(UTC) - last_update
    return age.total_seconds() > _STALE_AFTER_SECONDS


def _render_active_run(run: dict) -> None:
    """Render a running (or possibly stalled) run's progress.

    Args:
        run: A `GET /skills/extraction-runs/active` response body.
    """
    total = run["total_pending"] or 1
    done = run["extracted_count"] + run["failed_count"]
    st.progress(
        min(done / total, 1.0),
        text=f"{run['extracted_count']} extracted, "
        f"{run['failed_count']} failed, out of {run['total_pending']}",
    )
    if done >= run["total_pending"]:
        st.caption("All jobs processed — mapping skills to ESCO…")
    if _is_stale(run["updated_at"]):
        st.warning(
            "This run's progress hasn't updated in over half an hour — the "
            "API may have restarted. Already-extracted jobs are safe, but "
            "Stop won't clear this row on its own if nothing is left "
            "running to act on the request — see the User Guide."
        )
    if st.button("Stop", key="stop_run"):
        response = _post(f"/skills/extraction-runs/{run['run_id']}/cancel")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            st.error(f"Failed to stop: {_error_message(exc)}")
        else:
            st.rerun()


def _render_terminal_run(run: dict) -> None:
    """Render a finished run's terminal state and a way to dismiss it.

    A run's terminal state (including its error, for a `failed` run)
    was previously never shown — `GET /active` only returns a
    `running` row, so the moment a run ended the UI had nothing left
    to poll and silently fell back to the start form. This renders
    whatever `GET /skills/extraction-runs/{run_id}` last returned for
    a `completed`, `cancelled`, or `failed` run.

    Args:
        run: A `GET /skills/extraction-runs/{run_id}` response body
            whose `status` is `completed`, `cancelled`, or `failed`.
    """
    summary = (
        f"{run['extracted_count']} extracted, {run['failed_count']} failed, "
        f"out of {run['total_pending']}."
    )
    if run["status"] == "failed":
        st.error(f"Run failed: {run['error_message']}\n\n{summary}")
    elif run["status"] == "cancelled":
        st.info(f"Run stopped. {summary}")
    else:
        st.success(f"Run completed. {summary}")
        if run["failed_count"]:
            st.caption(
                f"{run['failed_count']} job(s) could not be extracted and stay "
                "pending; the next run retries them."
            )
    if run.get("mapping_summary"):
        st.info(run["mapping_summary"])
    if run["status"] == "completed":
        st.caption(
            "The job–skill bridge is not refreshed automatically. From "
            f"`job_search/`, run: `{_DBT_REFRESH_COMMAND}`"
        )
    if st.button("Dismiss", key="dismiss_run"):
        del st.session_state["extraction_run_id"]
        st.rerun()


def _render_start_form() -> None:
    """Render the source/country scope picker and Start button."""
    try:
        filters = _get("/skills/extraction-runs/filters")
    except httpx.HTTPError as exc:
        st.error(f"Failed to load filter options: {exc}")
        return
    sources = st.multiselect("Sources", options=filters["sources"])
    countries = st.multiselect("Countries", options=filters["countries"])
    params = {}
    if sources:
        params["sources"] = sources
    if countries:
        params["countries"] = countries
    try:
        pending = _get("/skills/extraction-runs/pending-count", params=params)
        st.write(f"{pending['pending']} job(s) match this scope.")
    except httpx.HTTPError as exc:
        st.error(f"Failed to count pending jobs: {exc}")
        return
    ollama_location = st.selectbox(
        "Ollama location",
        options=list(_OLLAMA_LOCATION_LABELS),
        format_func=lambda key: _OLLAMA_LOCATION_LABELS[key],
        key="ollama_location",
    )
    if st.button("Start", key="start_run", disabled=pending["pending"] == 0):
        response = _post(
            "/skills/extraction-runs",
            {
                "sources": sources or None,
                "countries": countries or None,
                "ollama_location": ollama_location,
            },
        )
        if response.status_code == 409:
            st.error("A run is already active.")
        else:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                st.error(f"Failed to start: {_error_message(exc)}")
            else:
                st.session_state["extraction_run_id"] = response.json()["run_id"]
                st.rerun()


with st.expander("User Guide", expanded=False):
    st.markdown(_USER_GUIDE)

# A run started by this browser session is tracked by id so its terminal
# state (including a `failed` run's error) is always shown, not just while
# it's `running` — `GET /active` alone goes back to null the instant a run
# ends, which used to leave the UI with no path back to that outcome. If
# this session has no tracked run, `GET /active` still covers "someone else
# started one" or "a page reload lost session state" by adopting whatever
# is active into `extraction_run_id` so the rest of the flow is unified.
if "extraction_run_id" not in st.session_state:
    try:
        active_run = _get("/skills/extraction-runs/active")
    except httpx.HTTPError as exc:
        st.error(f"Failed to load run status: {exc}")
        active_run = None
    if active_run is not None:
        st.session_state["extraction_run_id"] = active_run["run_id"]

run_id = st.session_state.get("extraction_run_id")
if run_id is not None:
    try:
        run = _get(f"/skills/extraction-runs/{run_id}")
    except httpx.HTTPError as exc:
        st.error(f"Failed to load run status: {exc}")
        run = None
    if run is None:
        # Unknown run id — nothing to show or dismiss, fall back to the form.
        del st.session_state["extraction_run_id"]
        st.rerun()
    elif run["status"] == "running":
        _render_active_run(run)
        time.sleep(5)
        st.rerun()
    else:
        _render_terminal_run(run)
else:
    _render_start_form()
