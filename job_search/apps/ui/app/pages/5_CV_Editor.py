"""CV correction pass (PLAN.md Step 13) — upload a CV to extract a
truth base, or edit and re-save an existing one. Uses `st.data_editor`
for every list-shaped section (skills, experience bullets, education,
qualifications, publications) rather than one widget per nested field,
so adding/removing a row is a native grid action instead of bespoke
per-field UI.
"""

from __future__ import annotations

import time

import httpx
import pandas as pd
import streamlit as st

from core.cv.bullet_id import compute_bullet_id
from core.settings import get_settings

st.set_page_config(page_title="CV Editor", layout="wide")
st.title("CV Editor")

_settings = get_settings()


def _fetch_truth_base() -> dict | None:
    """Fetch the current CV truth base, if one has been extracted yet.

    Returns:
        The parsed `GET /cv/truth-base` response body.

    Raises:
        httpx.HTTPError: If the request fails.
    """
    response = httpx.get(f"{_settings.api_base_url}/cv/truth-base", timeout=10.0)
    response.raise_for_status()
    return response.json()


_STEP_LABELS: dict[str, str] = {
    "parsing_document": "Parsing document",
    "extracting_fields": "Extracting fields",
    "saving": "Saving",
}

_STEP_ICONS: dict[str, str] = {
    "pending": "⬜",
    "running": "⏳",
    "done": "✅",
    "failed": "❌",
}


def _poll_extraction_job(job_id: str) -> dict:
    """Fetch one extraction job's current status.

    Args:
        job_id: The job id returned by `POST /cv/extract`.

    Returns:
        The parsed `GET /cv/extract/jobs/{job_id}` response body.

    Raises:
        httpx.HTTPError: If the request fails, including a 404 for an
            unknown/expired job id.
    """
    response = httpx.get(
        f"{_settings.api_base_url}/cv/extract/jobs/{job_id}", timeout=10.0
    )
    response.raise_for_status()
    return response.json()


def _render_job_progress(job: dict) -> None:
    """Render an extraction job's steps as a live checklist.

    Args:
        job: A `GET /cv/extract/jobs/{job_id}` response body.
    """
    state = {
        "queued": "running",
        "running": "running",
        "succeeded": "complete",
        "failed": "error",
    }[job["status"]]
    with st.status(f"Extracting CV — {job['status']}", state=state, expanded=True):
        for step in job["steps"]:
            label = _STEP_LABELS.get(step["name"], step["name"])
            icon = _STEP_ICONS[step["status"]]
            if step["duration_seconds"] is not None:
                st.write(f"{icon} {label} ({step['duration_seconds']:.1f}s)")
            else:
                st.write(f"{icon} {label}")


def _upload_and_start_extraction() -> None:
    """Render a CV uploader and, on submit, start an extraction job.

    Stores the returned job id in session state and reruns so the
    top-level polling block picks it up immediately.
    """
    uploaded = st.file_uploader("Upload CV (PDF)", type=["pdf"], key="cv_uploader")
    if uploaded is not None and st.button("Extract", key="extract_button"):
        try:
            response = httpx.post(
                f"{_settings.api_base_url}/cv/extract",
                files={"file": (uploaded.name, uploaded.getvalue())},
                timeout=10.0,
            )
            response.raise_for_status()
            st.session_state["cv_extraction_job_id"] = response.json()["job_id"]
            st.rerun()
        except httpx.HTTPError as exc:
            st.error(f"Failed to start extraction: {exc}")


try:
    current = _fetch_truth_base()
except httpx.HTTPError as exc:
    st.error(f"Failed to load CV truth base: {exc}")
    current = None

job_id = st.session_state.get("cv_extraction_job_id")
if job_id is not None:
    try:
        job = _poll_extraction_job(job_id)
    except httpx.HTTPError as exc:
        st.error(f"Lost track of the extraction job — please retry: {exc}")
        del st.session_state["cv_extraction_job_id"]
    else:
        _render_job_progress(job)
        if job["status"] in {"queued", "running"}:
            time.sleep(1)
            st.rerun()
        elif job["status"] == "succeeded":
            del st.session_state["cv_extraction_job_id"]
            st.success(f"Extracted as version {job['version']}.")
            st.rerun()
        else:
            del st.session_state["cv_extraction_job_id"]
            failed_step = _STEP_LABELS.get(job["failed_step"], job["failed_step"])
            st.error(f"Extraction failed at {failed_step}: {job['error']}")
elif current is None:
    st.info("No CV on file yet — upload one to extract a truth base.")
    _upload_and_start_extraction()
else:
    truth_base = current["truth_base"]
    st.caption(f"Version {current['version']}")

    with st.expander("Re-extract from a new CV"):
        _upload_and_start_extraction()

    identity = st.text_input("Identity", value=truth_base["identity"])
    headline = st.text_input("Headline", value=truth_base["headline"])
    email = st.text_input("Email", value=truth_base["email"] or "")
    phone = st.text_input("Phone", value=truth_base["phone"] or "")
    linkedin_url = st.text_input("LinkedIn URL", value=truth_base["linkedin_url"] or "")
    nationality = st.text_input("Nationality", value=truth_base["nationality"] or "")
    summary = st.text_area("Summary", value=truth_base["summary"] or "", height=120)

    st.subheader("Skills")
    skills_df = pd.DataFrame(
        [
            {
                "name": s["name"],
                "years": s["years"],
                "last_used": s["last_used"],
            }
            for s in truth_base["skills"]
        ],
        columns=["name", "years", "last_used"],
    )
    edited_skills = st.data_editor(skills_df, num_rows="dynamic", key="skills_editor")

    st.subheader("Experience")
    experience_rows = []
    for exp_index, exp in enumerate(truth_base["experience"]):
        with st.expander(f"{exp['company']} — {exp['title']}", expanded=False):
            company = st.text_input(
                "Company", value=exp["company"], key=f"company_{exp_index}"
            )
            title = st.text_input("Title", value=exp["title"], key=f"title_{exp_index}")
            start = st.text_input("Start", value=exp["start"], key=f"start_{exp_index}")
            end = st.text_input("End", value=exp["end"] or "", key=f"end_{exp_index}")
            bullets_df = pd.DataFrame(
                [{"text": b["text"]} for b in exp["bullets"]], columns=["text"]
            )
            edited_bullets = st.data_editor(
                bullets_df, num_rows="dynamic", key=f"bullets_{exp_index}"
            )
            experience_rows.append(
                {
                    "company": company,
                    "title": title,
                    "start": start,
                    "end": end or None,
                    "bullets": edited_bullets["text"].tolist(),
                    "tech": exp["tech"],
                    "metrics": exp["metrics"],
                }
            )

    st.subheader("Education")
    education_df = pd.DataFrame(
        [
            {
                "institution": e["institution"],
                "qualification": e["qualification"],
                "start": e["start"],
                "end": e["end"],
            }
            for e in truth_base["education"]
        ],
        columns=["institution", "qualification", "start", "end"],
    )
    edited_education = st.data_editor(
        education_df, num_rows="dynamic", key="education_editor"
    )

    st.subheader("Publications")
    publications_df = pd.DataFrame(
        [{"citation": p["citation"]} for p in truth_base["publications"]],
        columns=["citation"],
    )
    edited_publications = st.data_editor(
        publications_df, num_rows="dynamic", key="publications_editor"
    )

    st.subheader("Professional Qualifications & Continuous Personal Development")
    qualifications_df = pd.DataFrame(
        [{"name": q["name"], "year": q["year"]} for q in truth_base["qualifications"]],
        columns=["name", "year"],
    )
    edited_qualifications = st.data_editor(
        qualifications_df, num_rows="dynamic", key="qualifications_editor"
    )

    st.subheader("Projects")
    projects_df = pd.DataFrame(
        [
            {
                "name": p["name"],
                "description": p["description"],
                "tech": ", ".join(p["tech"]),
                "url": p["url"] or "",
            }
            for p in truth_base["projects"]
        ],
        columns=["name", "description", "tech", "url"],
    )
    edited_projects = st.data_editor(
        projects_df, num_rows="dynamic", key="projects_editor"
    )

    st.subheader("Activities & Interests")
    activities_interests_df = pd.DataFrame(
        [{"text": a} for a in truth_base["activities_interests"]], columns=["text"]
    )
    edited_activities_interests = st.data_editor(
        activities_interests_df, num_rows="dynamic", key="activities_interests_editor"
    )

    if st.button("Save corrections"):
        new_truth_base = {
            "identity": identity,
            "headline": headline,
            "email": email or None,
            "phone": phone or None,
            "linkedin_url": linkedin_url or None,
            "nationality": nationality or None,
            "summary": summary or None,
            "locations": truth_base["locations"],
            "work_auth": truth_base["work_auth"],
            "skills": [
                {
                    "name": row["name"],
                    "canonical_id": None,
                    "years": row["years"],
                    "last_used": row["last_used"],
                    "evidence_refs": [],
                }
                for row in edited_skills.to_dict("records")
            ],
            "experience": [
                {
                    **row,
                    # Recomputed, not carried over from the pre-edit
                    # bullets: compute_bullet_id is deterministic, so an
                    # unedited bullet gets back the exact ID it already
                    # had, and an edited or reordered one correctly gets
                    # a new one (see core.cv.bullet_id's docstring).
                    # The first argument is the *experience entry's*
                    # index (matching extract.py's own call), not the
                    # bullet's position within it — every bullet in one
                    # experience entry shares the same experience_index
                    # and is distinguished from its siblings by text.
                    "bullets": [
                        {"bullet_id": compute_bullet_id(exp_index, text), "text": text}
                        for text in row["bullets"]
                    ],
                }
                for exp_index, row in enumerate(experience_rows)
            ],
            "education": [
                {
                    "institution": row["institution"],
                    "qualification": row["qualification"],
                    "start": row["start"],
                    "end": row["end"],
                }
                for row in edited_education.to_dict("records")
            ],
            "publications": [
                {"citation": row["citation"]}
                for row in edited_publications.to_dict("records")
            ],
            "qualifications": [
                {"name": row["name"], "year": row["year"]}
                for row in edited_qualifications.to_dict("records")
            ],
            "projects": [
                {
                    "name": row["name"],
                    "description": row["description"],
                    "tech": [t.strip() for t in row["tech"].split(",") if t.strip()],
                    "url": row["url"] or None,
                }
                for row in edited_projects.to_dict("records")
            ],
            "activities_interests": edited_activities_interests["text"].tolist(),
        }
        try:
            response = httpx.put(
                f"{_settings.api_base_url}/cv/truth-base",
                json={
                    "extracted_markdown": current["extracted_markdown"],
                    "truth_base": new_truth_base,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            st.success(f"Saved as version {response.json()['version']}.")
            st.rerun()
        except httpx.HTTPError as exc:
            st.error(f"Save failed: {exc}")
