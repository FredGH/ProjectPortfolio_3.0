"""Categorisation review — hand-check dim_job's category/seniority_band
assignments (PLAN.md Step 11a's "Done when": >=90% agreement over 100
hand-checked classifications, tracked as JOB-170). Loads a stratified
sample across category x category_method with low-confidence rows
biased to the front — see GET /classification/jobs-to-review's own
docstring for the exact rule.
"""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings
from core.text import readable_description

_CATEGORIES = (
    "software_engineer",
    "data_engineer",
    "data_scientist",
    "ai_ml_engineer",
    "analytics_engineer",
    "platform_devops",
    "other",
)
_SENIORITY_BANDS = ("junior", "mid", "senior", "lead", "principal")

st.set_page_config(page_title="Categorisation Review", layout="wide")
st.title("Categorisation Review")
st.write(
    "Confirm or correct each job's category and seniority band. Both "
    "dropdowns start pre-filled with the pipeline's own answer — "
    "submit as-is when it's right, change it first when it's not."
)

_settings = get_settings()

if "review_jobs" not in st.session_state:
    st.session_state.review_jobs = []
    st.session_state.review_index = 0

reviewed_by = st.text_input("Your name (for the audit trail)", key="reviewed_by")

try:
    summary_response = httpx.get(
        f"{_settings.api_base_url}/classification/review-summary", timeout=10.0
    )
    summary_response.raise_for_status()
    summary = summary_response.json()
    if summary["reviewed_count"]:
        rate = summary["agreement_rate"]
        st.metric(
            "Agreement so far",
            f"{rate:.0%}",
            help=f"{summary['agree_count']} of {summary['reviewed_count']} reviewed",
        )
        if summary["reviewed_count"] >= 100:
            if rate >= 0.90:
                st.success(
                    f"{summary['reviewed_count']} reviewed at {rate:.0%} "
                    "agreement — JOB-170's >=90% target is met."
                )
            else:
                st.warning(
                    f"{summary['reviewed_count']} reviewed at {rate:.0%} "
                    "agreement — below JOB-170's 90% target."
                )
except httpx.HTTPError as exc:
    st.error(f"Failed to load review summary: {exc}")

if st.button("Load jobs") or not st.session_state.review_jobs:
    try:
        response = httpx.get(
            f"{_settings.api_base_url}/classification/jobs-to-review",
            params={"limit": 100},
            timeout=30.0,
        )
        response.raise_for_status()
        st.session_state.review_jobs = response.json()
        st.session_state.review_index = 0
    except httpx.HTTPError as exc:
        st.error(f"Failed to load jobs: {exc}")

jobs = st.session_state.review_jobs
index = st.session_state.review_index

if not jobs:
    st.success("No jobs need reviewing right now.")
elif index >= len(jobs):
    st.success(
        f"Done — reviewed all {len(jobs)} loaded jobs. Click 'Load jobs' for more."
    )
else:
    job = jobs[index]
    st.progress(index / len(jobs), text=f"Job {index + 1} of {len(jobs)}")

    st.subheader(job["title_for_display"] or job["title_raw"] or "(no title)")
    meta_cols = st.columns(4)
    meta_cols[0].write(f"Company: {job['company'] or '(none)'}")
    meta_cols[1].write(f"Location: {job['location'] or '(none)'}")
    meta_cols[2].write(f"Pipeline method: {job['category_method']}")
    meta_cols[3].write(f"Confidence: {job['category_confidence']:.2f}")

    description = job["description"]
    st.text_area(
        "Description",
        value=readable_description(description) if description else "(none)",
        height=250,
        disabled=True,
        key=f"desc_{job['job_group_id']}_{index}",
    )

    form_cols = st.columns(2)
    category = form_cols[0].selectbox(
        "Category",
        _CATEGORIES,
        index=_CATEGORIES.index(job["category"]),
        key=f"category_{job['job_group_id']}_{index}",
    )
    seniority_band = form_cols[1].selectbox(
        "Seniority band",
        _SENIORITY_BANDS,
        index=_SENIORITY_BANDS.index(job["seniority_band"]),
        key=f"seniority_{job['job_group_id']}_{index}",
    )
    notes = st.text_input(
        "Notes (optional — why this disagrees, if it does)",
        key=f"notes_{job['job_group_id']}_{index}",
    )

    def _submit_review() -> bool:
        """Post the current job's verdict and advance to the next one.

        Returns:
            True if the review was saved (and the index advanced),
            False if the save failed (the index is left unchanged so
            the job can be retried).
        """
        try:
            response = httpx.post(
                f"{_settings.api_base_url}/classification/reviews",
                json={
                    "job_group_id": job["job_group_id"],
                    "reviewed_category": category,
                    "reviewed_seniority_band": seniority_band,
                    "reviewed_by": reviewed_by or None,
                    "notes": notes or None,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            st.session_state.review_index += 1
            return True
        except httpx.HTTPError as exc:
            st.error(f"Failed to save review: {exc}")
            return False

    button_cols = st.columns(2)
    if button_cols[0].button("Submit review", use_container_width=True):
        if _submit_review():
            st.rerun()
    if button_cols[1].button("Skip", use_container_width=True):
        st.session_state.review_index += 1
        st.rerun()
