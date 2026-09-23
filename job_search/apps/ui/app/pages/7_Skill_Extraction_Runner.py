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
_STALE_AFTER_SECONDS = 120

_USER_GUIDE = """
Runs `extract-job-skills` for the sources/countries you pick, in
bounded batches of 30 jobs with a pause between each — the same
protection `scripts/extract_in_batches.sh` uses, so it's safe to run
even though it can take a long time (a local model on CPU takes
minutes per job).

Only one run can be active at a time. **Stop** finishes the job
currently in progress, then halts — nothing already extracted is
undone, and a stopped run can always be restarted later to pick up
where it left off.

If a run shows as **possibly stalled**, the API process restarted
while it was running (its progress hasn't moved in over two minutes).
The jobs it already extracted are safe; **Cancel** just clears the
stuck row so a new run can start.
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
    if _is_stale(run["updated_at"]):
        st.warning(
            "This run's progress hasn't updated in over two minutes — the "
            "API may have restarted. Already-extracted jobs are safe; "
            "Cancel to clear this run so a new one can start."
        )
    if st.button("Stop", key="stop_run"):
        response = _post(f"/skills/extraction-runs/{run['run_id']}/cancel")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            st.error(f"Failed to stop: {_error_message(exc)}")
        else:
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
    if st.button("Start", key="start_run", disabled=pending["pending"] == 0):
        response = _post(
            "/skills/extraction-runs",
            {"sources": sources or None, "countries": countries or None},
        )
        if response.status_code == 409:
            st.error("A run is already active.")
        else:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                st.error(f"Failed to start: {_error_message(exc)}")
            else:
                st.rerun()


with st.expander("User Guide", expanded=False):
    st.markdown(_USER_GUIDE)

try:
    active_run = _get("/skills/extraction-runs/active")
except httpx.HTTPError as exc:
    st.error(f"Failed to load run status: {exc}")
    active_run = None

if active_run is not None:
    _render_active_run(active_run)
    if active_run["status"] == "running":
        time.sleep(5)
        st.rerun()
else:
    _render_start_form()
