"""Tailored CV review — pick a top-scored job, tailor your CV to it, and
decide every line the fabrication guard could not trace to your CV
(PLAN.md Step 17).
"""

from __future__ import annotations

import time

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

A **stretch** warning means the job's title implies more seniority or
scope than your CV shows. That is advice, not an error.
"""
    )

_settings = get_settings()
_base = _settings.api_base_url
_POLL_SECONDS = 3

_flash = st.session_state.pop("tailoring_flash", None)
if _flash:
    st.error(_flash)


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
        return str(response.json().get("detail", response.status_code))
    except ValueError:
        return f"HTTP {response.status_code}"


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
except httpx.HTTPError as exc:
    st.error(f"Failed to load your jobs: {exc}")
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

if st.button("Tailor my CV to this job", key="tailor-start", type="primary"):
    started = _post("/tailoring/runs", {"job_group_id": selected})
    if started.status_code == 202:
        st.session_state["tailoring_run_id"] = started.json()["run_id"]
    else:
        st.session_state["tailoring_flash"] = f"Could not start: {_detail(started)}"
    st.rerun()

run_id = st.session_state.get("tailoring_run_id") or chosen["latest_run_id"]
if not run_id:
    st.caption("Not tailored yet.")
    st.stop()

try:
    run_response = _get(f"/tailoring/runs/{run_id}")
    run_response.raise_for_status()
    run = run_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load the tailored CV: {exc}")
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
st.markdown(f"**Status:** {status} · attempts: {run['attempts']}")

if status == "generating":
    st.caption("Working… this page refreshes on its own.")
    time.sleep(_POLL_SECONDS)
    st.rerun()

if status == "failed":
    st.error(f"This run failed: {run['error_message']}")
    st.stop()

document = run["document"]
if document is None:
    st.stop()

if document["stretch"]["is_stretch"]:
    st.warning(f"Stretch: {document['stretch']['reason']}")

coverage = document["keyword_coverage"]
st.subheader("Keyword coverage")
st.caption(f"Covered: {', '.join(coverage['covered']) or 'none'}")
if coverage["missing_evidenced"]:
    st.caption(
        "Your CV evidences these, but the tailored CV doesn't show them: "
        + ", ".join(coverage["missing_evidenced"])
    )
if coverage["missing_unevidenced"]:
    st.caption(
        "The job asks for these, but your CV doesn't evidence them (never "
        "added): " + ", ".join(coverage["missing_unevidenced"])
    )

st.subheader("Tailored CV")
st.text(document["headline"])
if document["summary"]:
    st.text(document["summary"]["text"])
    st.caption(f"summary · {document['summary']['origin']}")
for role in document["experience"]:
    st.markdown("---")
    st.text(f"{role['title']} — {role['company']}")
    st.caption(f"{role['start'] or '?'} – {role['end'] or 'present'}")
    for bullet in role["bullets"]:
        st.text(bullet["text"])
        refs = ", ".join(bullet["evidence_refs"]) or "no source"
        st.caption(f"{bullet['origin']} · source: {refs}")
st.markdown("---")
st.text("Skills: " + ", ".join(skill["name"] for skill in document["skills"]))

pending = [o for o in run["orphans"] if o["status"] == "pending"]
if status == "approved":
    st.success("Approved — every line traces to your CV.")
if pending:
    st.subheader("Needs your decision")
sources = run["sources"]
for orphan in pending:
    st.markdown("---")
    where = (
        "Summary"
        if orphan["section"] == "summary"
        else f"Role {orphan['experience_index']}, bullet {orphan['bullet_index']}"
    )
    st.text(f"{where} ({orphan['kind']})")
    st.text(orphan["text"])
    if orphan["issue"]:
        st.caption(orphan["issue"])
    options = [
        s
        for s in sources
        if orphan["section"] == "summary"
        or s["experience_index"] == orphan["experience_index"]
    ]
    choice = st.selectbox(
        "Evidenced by",
        options=options,
        format_func=lambda s: f"{s['role']}: {s['text']}",
        key=f"link-select-{orphan['id']}",
    )
    link_col, reject_col, _ = st.columns([1, 1, 4])
    if link_col.button("Link", key=f"link-{orphan['id']}", disabled=choice is None):
        decided = _post(
            f"/tailoring/orphans/{orphan['id']}/decision",
            {"action": "link", "evidence_ref": choice["bullet_id"]},
        )
        if decided.status_code != 200:
            st.session_state["tailoring_flash"] = _detail(decided)
        st.rerun()
    if reject_col.button("Reject", key=f"reject-{orphan['id']}"):
        decided = _post(
            f"/tailoring/orphans/{orphan['id']}/decision", {"action": "reject"}
        )
        if decided.status_code != 200:
            st.session_state["tailoring_flash"] = _detail(decided)
        st.rerun()
