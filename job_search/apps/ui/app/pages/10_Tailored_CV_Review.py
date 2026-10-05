"""Tailored CV review — pick a top-scored job, tailor your CV to it, and
decide every line the fabrication guard could not trace to your CV
(PLAN.md Step 17).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx
import streamlit as st

from core.settings import get_settings
from core.ui.theme import apply_theme

st.set_page_config(page_title="Tailored CV Review", layout="wide")
apply_theme()
st.title("Tailored CV Review")

with st.expander("User manual"):
    st.markdown(
        """
Pick one of your top-scored jobs and click **Tailor**. The tailored CV is
built from your CV: companies, titles and dates are copied, never
generated, and the headline is exactly the job's title. A reviewer model
then checks that every reworded bullet claims nothing its source bullets
don't.

Anything that can't be traced to your CV shows up under **Needs your
decision**. For each line either **Link** it to the bullet in your CV that
evidences it (the case where your CV states it obliquely), or **Reject**
it. Nothing is written to your CV itself. The tailored CV is **approved**
once no line is waiting.

Use **Run the Tailor on** to choose who rewrites the CV: Claude, Ollama
on this Mac, or the Docker Ollama service (CPU only, slow). The fact
checker always runs on Claude. **Cancel run** stops a run at once.

A **stretch** warning means the job's title implies more seniority or
scope than your CV shows. That is advice, not an error.
"""
    )

_settings = get_settings()
_base = _settings.api_base_url
_POLL_SECONDS = 3
_MAX_SOURCE_CHARS = 160
# Minutes without activity before the page doubts a run is alive: just above
# the 2000 s (~33 min) client timeout of one local Ollama call, during which
# nothing is written. (A comment, not a bare string: Streamlit "magic" would
# render a bare string on the page.)
STALE_AFTER_MINUTES = 35

_MD_META = set("\\`*_{}[]()#+-.!|<>~$:&")


def _plain(text: object) -> str:
    """Escape text so Streamlit renders it literally, never as markdown.

    `st.error`, `st.warning` and friends render GitHub-flavoured markdown;
    API, CV, job and LLM text must not become links, images or emoji.

    Args:
        text: Untrusted text (anything `str()`-able).

    Returns:
        The text with every markdown, LaTeX and shortcode metacharacter
        backslash-escaped.
    """
    return "".join(f"\\{ch}" if ch in _MD_META else ch for ch in str(text))


def _seconds_since(value: object) -> int | None:
    """Whole seconds from an API timestamp to now, never negative.

    Args:
        value: An ISO-8601 string from the API (naive means UTC), or
            anything else.

    Returns:
        Elapsed seconds (0 on clock skew), or None when `value` is not a
        usable timestamp.
    """
    if not isinstance(value, str):
        return None
    try:
        then = datetime.fromisoformat(value)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - then).total_seconds()))


def _clock(seconds: int) -> str:
    """Format seconds as MM:SS.

    Args:
        seconds: A non-negative duration.

    Returns:
        `MM:SS` (minutes may exceed two digits).
    """
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _show_progress(run: dict) -> None:
    """Show what a `generating` run is doing right now.

    Everything derived from the API goes through `st.text` / `_plain`.
    A missing or malformed shape falls back to the minimal caption.

    Args:
        run: The run from `GET /tailoring/runs/{id}`.
    """
    progress = run.get("progress")
    if progress is None:
        message, history = "Starting…", []
    elif isinstance(progress, dict) and isinstance(progress.get("message"), str):
        message = progress["message"]
        raw = progress.get("history")
        history = (
            [h for h in raw if isinstance(h, str)] if isinstance(raw, list) else []
        )
    else:
        st.caption("Working… this page refreshes on its own.")
        return
    st.text(message)
    for line in history:
        st.text(line)
    running = _seconds_since(run.get("started_at"))
    idle = _seconds_since(run.get("updated_at"))
    if running is not None:
        st.text(f"Running for {_clock(running)}")
    if idle is not None:
        st.text(f"Last activity {_clock(idle)} ago")
        if idle > STALE_AFTER_MINUTES * 60:
            st.warning(
                _plain(
                    f"No activity for {idle // 60} minutes. A local model can take "
                    "up to ~33 minutes for one step, so this run may have stopped "
                    "(for example after the API restarted). If it stays like "
                    'this, click "Tailor my CV to this job" to start again.'
                )
            )
    st.caption(
        "A local model works through one step at a time and can take several "
        "minutes per attempt; the timer shows it is still running."
    )


_flash = st.session_state.pop("tailoring_flash", None)
if _flash:
    st.error(_plain(_flash))


def _get(path: str) -> httpx.Response:
    """GET from the API.

    Args:
        path: The path, starting with `/`.

    Returns:
        The response.
    """
    return httpx.get(f"{_base}{path}", timeout=10.0)


def _post(path: str, json: dict) -> httpx.Response:
    """POST to the API.

    Args:
        path: The path, starting with `/`.
        json: The JSON body.

    Returns:
        The response.
    """
    return httpx.post(f"{_base}{path}", json=json, timeout=10.0)


def _detail(response: httpx.Response) -> str:
    """Pull the API's error message out of a failed response.

    Args:
        response: A non-2xx response.

    Returns:
        The `detail` field when present, else the status code.
    """
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict):
        return str(body.get("detail", response.status_code))
    return f"HTTP {response.status_code}"


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_backends() -> list[dict]:
    """Fetch the Tailor backends, cached for 30 s.

    The status poll reruns the page every 3 s and each fetch makes the API
    probe two Ollama servers. A failure raises, so it is never cached.

    Returns:
        The backends (each with a string `id`, `label` and `detail`, a
        boolean `available`).

    Raises:
        httpx.HTTPError: If the call fails.
        ValueError: If the answer is empty or malformed.
    """
    response = _get("/tailoring/backends")
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise ValueError("no backends")
    for row in rows:
        if not (
            isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and isinstance(row.get("label"), str)
            and isinstance(row.get("detail"), str)
            and isinstance(row.get("available"), bool)
        ):
            raise ValueError("malformed backend")
    return rows


def _load_backends() -> list[dict] | None:
    """Fetch the Tailor backends from the API.

    Returns:
        The backends, or None when the call fails or the answer is empty or
        malformed, so the page falls back to the API's default.
    """
    try:
        return _fetch_backends()
    except (httpx.HTTPError, ValueError):
        return None


def _runs_locally(run: dict, backends: list[dict] | None) -> bool:
    """Tell whether a run's Tailor is a local model.

    A legacy run has no stored backend; the API then labels it with the
    default backend's label, which identifies the backend here.

    Args:
        run: The run from `GET /tailoring/runs/{id}`.
        backends: The backends, or None when they could not be loaded.

    Returns:
        True for a local (Ollama) Tailor.
    """
    backend_id = run.get("tailor_backend")
    if backend_id in ("native", "docker"):
        return True
    if backend_id is None and backends:
        label = run.get("tailor_label")
        for backend in backends:
            if backend["label"] == label:
                return backend.get("provider") == "ollama"
    return False


def _decide(orphan_id: str, body: dict) -> None:
    """Post an orphan decision, flash any failure, and rerun.

    Args:
        orphan_id: The orphan's id.
        body: The decision JSON body.
    """
    try:
        decided = _post(f"/tailoring/orphans/{orphan_id}/decision", body)
        if decided.status_code != 200:
            st.session_state["tailoring_flash"] = _detail(decided)
    except httpx.HTTPError as exc:
        st.session_state["tailoring_flash"] = f"Could not save the decision: {exc}"
    st.rerun()


def _cancel(run_id: str) -> None:
    """Cancel a running run, flash any failure, and rerun.

    Args:
        run_id: The run to cancel.
    """
    try:
        cancelled = _post(f"/tailoring/runs/{run_id}/cancel", {})
        if cancelled.status_code != 200:
            st.session_state["tailoring_flash"] = _detail(cancelled)
    except httpx.HTTPError as exc:
        st.session_state["tailoring_flash"] = f"Could not cancel: {exc}"
    st.rerun()


def _label(candidate: dict) -> str:
    """Build a candidate's picker label.

    Args:
        candidate: One item from `GET /tailoring/candidates`.

    Returns:
        `title — company — score (latest status)`.
    """
    status = f" ({candidate['latest_status']})" if candidate["latest_status"] else ""
    return (
        f"{candidate['title_for_display'] or '(no title)'} — "
        f"{candidate['company'] or 'unknown company'} — "
        f"{candidate['final_score']:.2f}{status}"
    )


try:
    response = _get("/tailoring/candidates")
    response.raise_for_status()
    candidates = response.json()
except (httpx.HTTPError, ValueError) as exc:
    st.error(_plain(f"Failed to load your jobs: {exc}"))
    st.stop()

if not candidates:
    st.info(
        "No scored jobs yet — run the scoring stages from the Pipeline "
        "Dashboard first."
    )
    st.stop()

labels = {c["job_group_id"]: _label(c) for c in candidates}
selected = st.selectbox(
    "Job",
    options=list(labels),
    format_func=lambda job_id: labels[job_id],
    key="tailoring_selected_job",
)
chosen = next(c for c in candidates if c["job_group_id"] == selected)

backends = _load_backends()
backend: dict | None = None
if backends is None:
    st.info("Could not load the Tailor backends; the default one will be used.")
else:
    by_id = {b["id"]: b for b in backends}
    default_index = next((i for i, b in enumerate(backends) if b.get("default")), 0)
    backend_id = st.selectbox(
        "Run the Tailor on",
        options=list(by_id),
        index=default_index,
        format_func=lambda bid: f"{'✓' if by_id[bid]['available'] else '✗'} "
        f"{by_id[bid]['label']}",
        key="tailoring_backend",
    )
    backend = by_id[backend_id]
    st.caption(_plain(backend["detail"]))
    if not backend["available"]:
        st.warning(_plain(f"{backend['label']} is not available: {backend['detail']}"))

if st.button(
    "Tailor my CV to this job",
    key="tailor-start",
    type="primary",
    disabled=backend is not None and not backend["available"],
):
    try:
        body = {"job_group_id": selected}
        if backend is not None:
            body["backend"] = backend["id"]
        started = _post("/tailoring/runs", body)
        if started.status_code == 202:
            st.session_state["tailoring_run_id"] = started.json()["run_id"]
        else:
            st.session_state["tailoring_flash"] = f"Could not start: {_detail(started)}"
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        st.session_state["tailoring_flash"] = f"Could not start: {exc}"
    st.rerun()

run_id = st.session_state.get("tailoring_run_id") or chosen["latest_run_id"]
if not run_id:
    st.caption("Not tailored yet.")
    st.stop()

try:
    run_response = _get(f"/tailoring/runs/{run_id}")
    run_response.raise_for_status()
    run = run_response.json()
except (httpx.HTTPError, ValueError) as exc:
    st.error(_plain(f"Failed to load the tailored CV: {exc}"))
    st.stop()

if run["job_group_id"] != selected:
    # A stale run id from another job — fall back to this job's latest.
    st.session_state.pop("tailoring_run_id", None)
    if chosen["latest_run_id"]:
        st.session_state["tailoring_run_id"] = chosen["latest_run_id"]
        st.rerun()
    st.caption("Not tailored yet.")
    st.stop()

status = run["status"]
st.markdown(f"**Status:** {_plain(status)} · attempts: {_plain(run['attempts'])}")
if run.get("tailor_label"):
    st.text(f"Tailor: {run['tailor_label']}")

if status == "generating":
    _show_progress(run)
    if _runs_locally(run, backends):
        st.caption(
            "Cancel stops the run at once and closes the connection to the "
            "local model, which stops generating within a few seconds. Cancel "
            "unloads the model from that Ollama server; other local tasks "
            "using the same model reload it (a few seconds)."
        )
    else:
        st.caption(
            "Cancel stops the run at once; a Claude call already in flight "
            "finishes in the background and its result is discarded."
        )
    if st.button("Cancel run", key="cancel-run"):
        _cancel(run_id)
    time.sleep(_POLL_SECONDS)
    st.rerun()

if status == "cancelled":
    st.info("This run was cancelled.")
    st.stop()

if status == "failed":
    st.error(_plain(f"This run failed: {run['error_message']}"))
    st.stop()

document = run["document"]
if document is None:
    st.stop()

if document["stretch"]["is_stretch"]:
    st.warning(_plain(f"Stretch: {document['stretch']['reason']}"))

coverage = document["keyword_coverage"]
st.subheader("Keyword coverage")
st.text(f"Covered: {', '.join(coverage['covered']) or 'none'}")
if coverage["missing_evidenced"]:
    st.text(
        "Your CV evidences these, but the tailored CV doesn't show them: "
        + ", ".join(coverage["missing_evidenced"])
    )
if coverage["missing_unevidenced"]:
    st.text(
        "The job asks for these, but your CV doesn't evidence them (never "
        "added): " + ", ".join(coverage["missing_unevidenced"])
    )

sources = run["sources"]
_source_text = {s["bullet_id"]: s["text"] for s in sources}


def _shorten(text: str) -> str:
    """Truncate a long source text for display.

    Args:
        text: A truth-base bullet text.

    Returns:
        The text, cut at `_MAX_SOURCE_CHARS` with an ellipsis when longer.
    """
    if len(text) <= _MAX_SOURCE_CHARS:
        return text
    return text[: _MAX_SOURCE_CHARS - 1].rstrip() + "…"


def _sources_of(refs: list[str]) -> str:
    """Render the truth-base text(s) a line's refs point to.

    Args:
        refs: `bullet_id`s cited by the line.

    Returns:
        The known source texts joined by " | ", or `no source` when none
        of the refs is known.
    """
    texts = [_shorten(_source_text[ref]) for ref in refs if ref in _source_text]
    return " | ".join(texts) or "no source"


def _role_name(experience_index: int | None) -> str:
    """Name a role of the tailored CV for an orphan header.

    Args:
        experience_index: The role's index in the document.

    Returns:
        `<title> at <company>`, or `Role <n>` when the index is unknown.
    """
    roles = document["experience"]
    if experience_index is not None and 0 <= experience_index < len(roles):
        role = roles[experience_index]
        return f"{role['title']} at {role['company']}"
    return f"Role {experience_index}"


st.subheader("Tailored CV")
st.text(document["headline"])
if document["summary"]:
    summary = document["summary"]
    st.text(summary["text"])
    summary_source = (
        "your CV's summary"
        if summary["origin"] == "original" and not summary["evidence_refs"]
        else _sources_of(summary["evidence_refs"])
    )
    st.text(f"summary · {summary['origin']} · source: {summary_source}")
for role in document["experience"]:
    st.markdown("---")
    st.text(f"{role['title']} — {role['company']}")
    st.text(f"{role['start'] or '?'} – {role['end'] or 'present'}")
    for bullet in role["bullets"]:
        st.text(bullet["text"])
        st.text(f"{bullet['origin']} · source: {_sources_of(bullet['evidence_refs'])}")
st.markdown("---")
st.text("Skills: " + ", ".join(skill["name"] for skill in document["skills"]))

pending = [o for o in run["orphans"] if o["status"] == "pending"]
if status == "approved":
    st.success("Approved — every line traces to your CV.")
if pending:
    st.subheader("Needs your decision")
_labels = {s["bullet_id"]: f"{s['role']}: {s['text']}" for s in sources}
for orphan in pending:
    st.markdown("---")
    where = (
        "Summary"
        if orphan["section"] == "summary"
        else _role_name(orphan["experience_index"])
    )
    st.text(f"{where} ({orphan['kind']})")
    st.text(orphan["text"])
    if orphan["kind"] == "unsupported":
        st.text(f"Claimed source: {_sources_of(orphan['claimed_refs'])}")
    if orphan["issue"]:
        st.text(orphan["issue"])
    options = [
        s["bullet_id"]
        for s in sources
        if orphan["section"] == "summary"
        or s["experience_index"] == orphan["experience_index"]
    ]
    # No preselection: linking must be a deliberate choice, never one click.
    choice = st.selectbox(
        "Evidenced by",
        options=options,
        index=None,
        placeholder="Choose the bullet that evidences this",
        format_func=lambda bullet_id: _labels[bullet_id],
        key=f"link-select-{orphan['id']}",
    )
    link_col, reject_col, _ = st.columns([1, 1, 4])
    if link_col.button("Link", key=f"link-{orphan['id']}", disabled=choice is None):
        _decide(orphan["id"], {"action": "link", "evidence_ref": choice})
    if reject_col.button("Reject", key=f"reject-{orphan['id']}"):
        _decide(orphan["id"], {"action": "reject"})
