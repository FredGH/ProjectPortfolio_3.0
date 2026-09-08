"""Dedup calibration — view the precision-recall curve from current
labels and record chosen auto-match/auto-reject thresholds (PLAN.md
Step 9). PLAN.md is explicit that cutoffs are chosen "from the curve,
not from intuition" — this page shows the curve; the human picks the
knee.
"""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Dedup Calibration", layout="wide")
st.title("Dedup Calibration")

_settings = get_settings()

try:
    thresholds_response = httpx.get(
        f"{_settings.api_base_url}/dedup/thresholds", timeout=10.0
    )
    thresholds_response.raise_for_status()
    current_thresholds = thresholds_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load current thresholds: {exc}")
    current_thresholds = None

if current_thresholds:
    st.info(
        f"Current: auto-match ≥ {current_thresholds['auto_match_threshold']:.3f}, "
        f"auto-reject ≤ {current_thresholds['auto_reject_threshold']:.3f} "
        f"(measured precision {current_thresholds['measured_precision']:.3f}, "
        f"recall {current_thresholds['measured_recall']:.3f}, from "
        f"{current_thresholds['labeled_pair_count']} labeled pairs, "
        f"as of {current_thresholds['calibrated_at']})"
    )
else:
    st.warning("No calibration run recorded yet — label some pairs first.")

try:
    curve_response = httpx.get(
        f"{_settings.api_base_url}/dedup/calibration", timeout=10.0
    )
    curve_response.raise_for_status()
    curve = curve_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load the calibration curve: {exc}")
    curve = []

if not curve:
    st.write("No labeled pairs yet.")
else:
    st.subheader(f"Precision/recall across {len(curve)} distinct thresholds")
    chart_data = {
        "threshold": [point["threshold"] for point in curve],
        "precision": [point["precision"] or 0.0 for point in curve],
        "recall": [point["recall"] or 0.0 for point in curve],
    }
    st.line_chart(chart_data, x="threshold", y=["precision", "recall"])
    st.dataframe(curve, use_container_width=True)

    st.subheader("Record a new calibration run")
    with st.form("record_calibration"):
        col1, col2 = st.columns(2)
        with col1:
            auto_match_threshold = st.number_input(
                "Auto-match threshold", min_value=0.0, max_value=1.0, value=0.9
            )
            measured_precision = st.number_input(
                "Measured precision at that threshold",
                min_value=0.0,
                max_value=1.0,
                value=0.95,
            )
        with col2:
            auto_reject_threshold = st.number_input(
                "Auto-reject threshold", min_value=0.0, max_value=1.0, value=0.5
            )
            measured_recall = st.number_input(
                "Measured recall at that threshold",
                min_value=0.0,
                max_value=1.0,
                value=0.8,
            )
        calibrated_by = st.text_input("Your name")
        submitted = st.form_submit_button("Save thresholds")

    if submitted:
        try:
            response = httpx.post(
                f"{_settings.api_base_url}/dedup/thresholds",
                json={
                    "auto_match_threshold": auto_match_threshold,
                    "auto_reject_threshold": auto_reject_threshold,
                    "measured_precision": measured_precision,
                    "measured_recall": measured_recall,
                    "labeled_pair_count": curve[-1]["predicted_match_count"],
                    "calibrated_by": calibrated_by or None,
                },
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            st.error(f"Failed to save thresholds: {exc}")
        else:
            st.success("Thresholds saved.")
            st.rerun()
