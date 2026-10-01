"""Scoring calibration — hand-label jobs, fit component weights, and
save the result (PLAN.md Step 16). Mirrors 3_Dedup_Calibration.py's
structure: a labeling flow, then a preview-before-save fit step.
"""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings
from core.ui.theme import apply_theme

st.set_page_config(page_title="Scoring Calibration", layout="wide")
apply_theme()
st.title("Scoring Calibration")

with st.expander("User manual"):
    st.markdown(
        """
This page calibrates how the four scoring components — vector
similarity, cross-encoder rerank, skill coverage, and LLM fit — are
weighted when blended into each job's final score. Until you calibrate,
every present component is weighted equally, which is a placeholder,
not a real preference.

**How it works:**

1. **Label 30 jobs** as `strong` / `maybe` / `no` — your honest judgment
   of how well each one fits you. Only jobs that made it all the way
   through the scoring funnel (all four components present) are shown.
2. Click **Preview calibration** once you've labeled at least 30. This
   fits weights against 20 of your labels and checks the result against
   the other 10 — labels the fitting process never saw — so the
   agreement figure reflects genuine generalization, not memorisation.
3. If the preview looks reasonable, click **Save weights** to apply it.
   Nothing is written until you do.

Saved weights take effect the next time the pipeline's `score-blend`
step runs for you.

**"No more eligible jobs to label right now"?** This means too few jobs
currently have all four components present at once — `score-similarity`
only gives a reranker score to its top N jobs (200 by default), which
may barely overlap with `score-skill-coverage`'s and `score-llm-rerank`'s
own top-K pools. Widen the pool from the CLI:

```bash
docker compose run --rm pipeline score-similarity --user-id <id> --top-n 800
docker compose run --rm pipeline score-llm-rerank --user-id <id>
docker compose run --rm pipeline score-blend --user-id <id>
```

`score-llm-rerank` makes real (small, but non-zero) Anthropic API calls
for up to 50 jobs each time — avoid running it more than you need to.
"""
    )

_settings = get_settings()
_base = _settings.api_base_url


def _get(path: str) -> httpx.Response:
    return httpx.get(f"{_base}{path}", timeout=10.0)


def _put(path: str, json: dict) -> httpx.Response:
    return httpx.put(f"{_base}{path}", json=json, timeout=10.0)


def _delete(path: str) -> httpx.Response:
    return httpx.delete(f"{_base}{path}", timeout=10.0)


def _post(path: str, json: dict | None = None) -> httpx.Response:
    return httpx.post(f"{_base}{path}", json=json, timeout=10.0)


st.subheader("1. Hand-label jobs")

try:
    labels_response = _get("/scoring/labels")
    labels_response.raise_for_status()
    labels = labels_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load existing labels: {exc}")
    labels = []

st.progress(min(len(labels) / 30, 1.0), text=f"{len(labels)} / 30 labeled")

# Streamlit re-runs this whole script on every interaction, including the
# run that processes a button click below -- fetching a *new* candidate at
# the top of that same run (pick_labeling_candidate picks uniformly at
# random) would silently label whatever job that fresh fetch happened to
# return, not the job the user actually saw and clicked for. Caching the
# candidate in session_state, and only clearing it after a label/un-label
# actually changes the pool, keeps the displayed job and the labeled job
# the same one across reruns.
if "calibration_candidate" not in st.session_state:
    try:
        candidate_response = _get("/scoring/labeling-candidate")
        candidate_response.raise_for_status()
    except httpx.HTTPError as exc:
        st.error(f"Failed to load a candidate: {exc}")
        candidate_response = None
    if candidate_response is not None and candidate_response.status_code == 204:
        st.session_state.calibration_candidate = None
    elif candidate_response is not None:
        st.session_state.calibration_candidate = candidate_response.json()

candidate = st.session_state.get("calibration_candidate")

if candidate is None:
    st.info("No more eligible jobs to label right now.")
else:
    st.markdown(f"### {candidate['title']} — {candidate['company']}")
    st.caption(f"{candidate['location']} · {candidate['engagement_type']}")
    with st.expander("Job description"):
        st.write(candidate["description"])
    with st.expander("Current scoring signals"):
        st.write(
            {
                "vector_similarity_score": candidate["vector_similarity_score"],
                "reranker_score": candidate["reranker_score"],
                "skill_coverage_score": candidate["skill_coverage_score"],
                "llm_fit_score": candidate["llm_fit_score"],
                "llm_rationale": candidate["llm_rationale"],
                "llm_missing_skills": candidate["llm_missing_skills"],
                "llm_stretch_flag": candidate["llm_stretch_flag"],
            }
        )
    col1, col2, col3 = st.columns(3)
    for col, label in ((col1, "strong"), (col2, "maybe"), (col3, "no")):
        with col:
            button_key = f"label-{label}-{candidate['job_group_id']}"
            if st.button(label.capitalize(), key=button_key, use_container_width=True):
                response = _put(
                    f"/scoring/labels/{candidate['job_group_id']}", {"label": label}
                )
                if response.status_code != 200:
                    st.error(f"Failed to save label: {response.text}")
                else:
                    del st.session_state["calibration_candidate"]
                    st.rerun()

if labels:
    with st.expander(f"Already labeled ({len(labels)})"):
        for label in labels:
            col1, col2 = st.columns([4, 1])
            col1.write(f"{label['job_group_id']} — **{label['label']}**")
            if col2.button("Un-label", key=f"unlabel-{label['job_group_id']}"):
                response = _delete(f"/scoring/labels/{label['job_group_id']}")
                if response.status_code != 200:
                    st.error(f"Failed to un-label: {response.text}")
                else:
                    # Un-labeling can make this job eligible again --
                    # discard the cached candidate so it's reconsidered.
                    st.session_state.pop("calibration_candidate", None)
                    st.rerun()

st.divider()
st.subheader("2. Fit & validate")

try:
    history_response = _get("/scoring/calibration-runs")
    history_response.raise_for_status()
    history = history_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load calibration history: {exc}")
    history = []

if history:
    latest_embedding_model = history[0]["embedding_model"]
    st.caption(
        f"Last saved: {history[0]['calibrated_at']} "
        f"(embedding model: {latest_embedding_model})"
    )
    try:
        current_model_response = _get("/scoring/embedding-model")
        current_model_response.raise_for_status()
        current_embedding_model = current_model_response.json()["embedding_model"]
    except httpx.HTTPError as exc:
        st.error(f"Failed to check the current embedding model: {exc}")
        current_embedding_model = latest_embedding_model
    if current_embedding_model != latest_embedding_model:
        st.warning(
            f"The embedding model has changed since your last saved "
            f"calibration ({latest_embedding_model} → "
            f"{current_embedding_model}) — PLAN.md Step 16 calls this out "
            f"explicitly: different vectors, different distances, invalid "
            f"weights. Recalibrating now is recommended."
        )

if len(labels) < 30:
    st.info(f"Label {30 - len(labels)} more job(s) to enable fitting.")
else:
    if st.button("Preview calibration"):
        response = _post("/scoring/calibrate")
        if response.status_code != 200:
            st.error(f"Failed to preview calibration: {response.text}")
        else:
            st.session_state["calibration_preview"] = response.json()

    preview = st.session_state.get("calibration_preview")
    if preview:
        st.write("**Fitted weights:**")
        st.write(preview["weights"])
        st.write(f"Fit set: {preview['fit_count']} jobs")
        st.write(f"Holdout set: {preview['holdout_count']} jobs")
        if preview["holdout_agreement"] is None:
            st.warning(
                "Holdout agreement is undefined — every holdout label was "
                "identical, so there's no rank variation to check against. "
                "Label a wider spread of strong/maybe/no before relying on "
                "this calibration."
            )
        else:
            st.metric("Holdout agreement (Spearman)", f"{preview['holdout_agreement']:.3f}")

        # The embedding-model staleness check runs proactively above, on
        # every page load whenever history exists -- not repeated here.

        calibrated_by = st.text_input("Your name")
        if st.button("Save weights"):
            response = _post(
                "/scoring/calibration-runs",
                {"preview": preview, "calibrated_by": calibrated_by or None},
            )
            if response.status_code != 200:
                st.error(f"Failed to save calibration: {response.text}")
            else:
                st.success(
                    "Weights saved. They take effect next time score-blend "
                    "runs for you."
                )
                del st.session_state["calibration_preview"]
                st.rerun()

if history:
    st.write("**Calibration history:**")
    st.dataframe(history, use_container_width=True)
