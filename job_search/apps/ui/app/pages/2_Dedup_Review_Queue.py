"""Dedup review queue — label candidate pairs as match/not-match
(PLAN.md Step 9). Runs in bootstrap mode (broad stratified sample)
until calibration_thresholds has a row, then narrows to the
auto-reject/auto-match middle band automatically — see
GET /dedup/pairs-to-label's own docstring for the exact rule.
"""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Dedup Review Queue", layout="wide")
st.title("Dedup Review Queue")
st.write(
    "Label each pair as the same job posting or not. Before any "
    "calibration run exists, pairs are sampled broadly across the "
    "score range for the initial 50-pair calibration exercise; "
    "afterwards, only the undecided middle band shows up here."
)

_settings = get_settings()

if "dedup_pairs" not in st.session_state:
    st.session_state.dedup_pairs = []
    st.session_state.dedup_pair_index = 0

labeled_by = st.text_input("Your name (for the audit trail)", key="labeled_by")

if st.button("Load pairs") or not st.session_state.dedup_pairs:
    try:
        response = httpx.get(
            f"{_settings.api_base_url}/dedup/pairs-to-label",
            params={"limit": 50},
            timeout=30.0,
        )
        response.raise_for_status()
        st.session_state.dedup_pairs = response.json()
        st.session_state.dedup_pair_index = 0
    except httpx.HTTPError as exc:
        st.error(f"Failed to load pairs: {exc}")

pairs = st.session_state.dedup_pairs
index = st.session_state.dedup_pair_index

if not pairs:
    st.success("No pairs need labeling right now.")
elif index >= len(pairs):
    st.success(
        f"Done — labeled all {len(pairs)} loaded pairs. Click 'Load pairs' for more."
    )
else:
    pair = pairs[index]
    st.progress((index) / len(pairs), text=f"Pair {index + 1} of {len(pairs)}")

    scores = pair["scores"]
    st.subheader(f"Blended score: {scores['blended_score']:.3f}")
    score_cols = st.columns(6)
    score_cols[0].metric("Company", f"{scores['company_similarity']:.2f}")
    score_cols[1].metric("Title", f"{scores['title_similarity']:.2f}")
    score_cols[2].metric("Description", f"{scores['description_similarity']:.2f}")
    score_cols[3].metric("Location", f"{scores['location_similarity']:.2f}")
    score_cols[4].metric("Date diff (days)", f"{scores['date_diff_days']:.1f}")
    score_cols[5].metric("Salary", f"{scores['salary_similarity']:.2f}")

    col_a, col_b = st.columns(2)
    for col, posting in ((col_a, pair["posting_a"]), (col_b, pair["posting_b"])):
        with col:
            st.markdown(f"**{posting['title'] or '(no title)'}**")
            st.write(f"Company: {posting['company'] or '(none)'}")
            st.write(f"Location: {posting['location'] or '(none)'}")
            st.write(f"Posted: {posting['posted_at'] or '(unknown)'}")
            st.write(f"URL: {posting['job_url_canonical']}")
            st.text_area(
                "Description",
                value=posting["description"] or "(none)",
                height=200,
                disabled=True,
                key=f"desc_{posting['job_key']}_{index}",
            )

    def _submit_label(label: str) -> None:
        """Post the current pair's label and advance to the next one."""
        try:
            response = httpx.post(
                f"{_settings.api_base_url}/dedup/labels",
                json={
                    "job_key_a": scores["job_key_a"],
                    "job_key_b": scores["job_key_b"],
                    "label": label,
                    "labeled_by": labeled_by or None,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            st.session_state.dedup_pair_index += 1
        except httpx.HTTPError as exc:
            st.error(f"Failed to save label: {exc}")

    button_cols = st.columns(3)
    if button_cols[0].button("✅ Same job (match)", use_container_width=True):
        _submit_label("match")
        st.rerun()
    if button_cols[1].button(
        "❌ Different jobs (not a match)", use_container_width=True
    ):
        _submit_label("not_match")
        st.rerun()
    if button_cols[2].button("⏭ Skip", use_container_width=True):
        st.session_state.dedup_pair_index += 1
        st.rerun()
