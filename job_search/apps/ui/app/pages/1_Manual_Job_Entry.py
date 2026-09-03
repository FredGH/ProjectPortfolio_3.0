"""Manual job entry — the paste form (PLAN.md Step 2)."""

from __future__ import annotations

import datetime

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Manual Job Entry", layout="wide")
st.title("Manual Job Entry")
st.write("Paste a job posting you found by browsing — LinkedIn has no usable API.")

_settings = get_settings()

try:
    _sources_response = httpx.get(f"{_settings.api_base_url}/sources", timeout=10.0)
    _sources_response.raise_for_status()
    _known_sources: list[str] = _sources_response.json()
except httpx.HTTPError:
    _known_sources = []

_NEW_SOURCE_SENTINEL = "+ Add new source"
_source_options = [*_known_sources, _NEW_SOURCE_SENTINEL]

with st.form("manual_job_entry", clear_on_submit=True):
    source_choice = st.selectbox(
        "Source name",
        options=_source_options,
        index=len(_source_options) - 1 if _known_sources else 0,
        help="Pick a prior source, or add a new one below.",
    )
    new_source_name = st.text_input(
        "New source name",
        placeholder="linkedin_manual, otta, recruiter_email...",
        disabled=source_choice != _NEW_SOURCE_SENTINEL,
    )
    source_name = (
        new_source_name if source_choice == _NEW_SOURCE_SENTINEL else source_choice
    )
    job_url = st.text_input("Job URL")
    job_spec = st.text_area("Job spec", height=300, help="Paste the full posting.")

    col1, col2, col3 = st.columns(3)
    with col1:
        posted_date = st.date_input("Posted date", value=datetime.date.today())
    with col2:
        company = st.text_input("Company (override)")
    with col3:
        title = st.text_input("Title (override)")

    location = st.text_input("Location (override)")
    notes = st.text_area("Notes", placeholder="via recruiter X, referral through Y...")

    submitted = st.form_submit_button("Ingest")

if submitted:
    if not source_name or not job_url or not job_spec:
        st.error("Source name, job URL, and job spec are all required.")
    else:
        payload = {
            "source_name": source_name,
            "job_url": job_url,
            "job_spec": job_spec,
            "posted_date": posted_date.isoformat(),
            "company": company or None,
            "title": title or None,
            "location": location or None,
            "notes": notes or None,
        }
        try:
            response = httpx.post(
                f"{_settings.api_base_url}/ingest/manual", json=payload, timeout=30.0
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            st.error(f"Ingestion failed: {exc}")
        else:
            body = response.json()
            st.success(
                f"Ingested — canonical URL: {body['job_url_canonical']} "
                f"(source_job_id: {body['source_job_id']})"
            )
            st.json(body)
