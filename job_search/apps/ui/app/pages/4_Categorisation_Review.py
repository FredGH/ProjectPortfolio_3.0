"""Categorisation review — hand-check dim_job's category/seniority_band
assignments (PLAN.md Step 11a's "Done when": >=90% agreement, tracked
as JOB-170; JOB-170's original wording fixed the sample at "100" — this
page instead computes a recommended sample size from the pool's actual
size via `core.stats.recommended_sample_size`, and lets a reviewer
override it). Loads a stratified sample across category x
category_method with low-confidence rows biased to the front — see
GET /classification/jobs-to-review's own docstring for the exact rule.

See the "How is this calculated?" expander for the sample-size formula.
A reviewer can override the recommended target and see, live, the
margin of error it implies; can also scope the whole exercise (target,
progress, jobs fetched) to one `country_iso`.
"""

from __future__ import annotations

import httpx
import streamlit as st

from core.classification.review import UNRESOLVED_COUNTRY as _UNRESOLVED_COUNTRY
from core.settings import get_settings
from core.stats import margin_of_error_for_sample_size, recommended_sample_size
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
_CONFIDENCE_LEVELS = (0.80, 0.90, 0.95, 0.99)

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
    st.session_state.review_submitted_count = 0
    st.session_state.review_baseline_count = 0
    st.session_state.review_loaded_region = object()  # never equals a real value


@st.cache_data(ttl=60)
def _fetch_regions(api_base_url: str) -> list[dict]:
    """Fetch the region picker's options.

    Cached for a minute — the set of `country_iso` values and their
    counts barely moves reviewer-to-reviewer, and every widget
    interaction on this page triggers a full Streamlit rerun, so an
    uncached fetch here would refire on things as unrelated as typing
    in the "Your name" box.

    Args:
        api_base_url: Cache key — also lets this be called without a
            module-level client the cache can't hash.

    Returns:
        The parsed `GET /classification/regions` response body.

    Raises:
        httpx.HTTPError: If the request fails.
    """
    response = httpx.get(f"{api_base_url}/classification/regions", timeout=10.0)
    response.raise_for_status()
    return response.json()


# --- Region picker --------------------------------------------------
try:
    region_rows = _fetch_regions(_settings.api_base_url)
except httpx.HTTPError as exc:
    st.error(f"Failed to load regions: {exc}")
    region_rows = []

_region_choices: dict[str, str | None] = {"All regions": None}
for row in sorted(region_rows, key=lambda r: r["total_count"], reverse=True):
    if row["country_iso"] is None:
        label = (
            f"Unclassified — no resolved country "
            f"({row['total_count']} total, {row['unreviewed_count']} to review)"
        )
        _region_choices[label] = _UNRESOLVED_COUNTRY
    else:
        label = (
            f"{row['country_iso']} "
            f"({row['total_count']} total, {row['unreviewed_count']} to review)"
        )
        _region_choices[label] = row["country_iso"]

region_label = st.selectbox("Region", list(_region_choices.keys()), key="review_region")
country_iso = _region_choices[region_label]


def _region_params(**extra: object) -> dict[str, object]:
    """Build query params, omitting `country_iso` entirely for "All
    regions" rather than sending it as `None`.

    httpx serializes a `None`-valued param as an empty string
    (`?country_iso=`), not an omitted one — FastAPI's
    `Query(default=None)` then sees `""`, not `None`, and the
    three-state SQL filter treats `""` as a (non-matching) country
    code instead of "no filter".

    Args:
        **extra: Any other query params to include as-is.

    Returns:
        `extra`, plus `country_iso` when it isn't `None`.
    """
    params = dict(extra)
    if country_iso is not None:
        params["country_iso"] = country_iso
    return params


reviewed_by = st.text_input("Your name (for the audit trail)", key="reviewed_by")

summary = None
try:
    summary_response = httpx.get(
        f"{_settings.api_base_url}/classification/review-summary",
        params=_region_params(),
        timeout=10.0,
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
except httpx.HTTPError as exc:
    st.error(f"Failed to load review summary: {exc}")

# --- Review target calculator ----------------------------------------
try:
    pool_preview = httpx.get(
        f"{_settings.api_base_url}/classification/jobs-to-review",
        params=_region_params(limit=1),
        timeout=10.0,
    )
    pool_preview.raise_for_status()
    unreviewed_count = pool_preview.json()["total_unreviewed_count"]
except httpx.HTTPError as exc:
    st.error(f"Failed to size the review pool: {exc}")
    unreviewed_count = 0

reviewed_count = summary["reviewed_count"] if summary else 0
population = reviewed_count + unreviewed_count

if population == 0:
    st.info("No categorized jobs in this region yet.")
    target = 0
else:
    with st.expander("How is this calculated?"):
        st.latex(r"n_0 = \frac{z^2 \cdot p(1-p)}{e^2}")
        st.latex(r"n = \dfrac{n_0}{1 + \dfrac{n_0 - 1}{N}}")
        st.write(
            "Standard sample-size-for-a-proportion formula, with a finite-"
            "population correction (`n`) since the pool (`N`) isn't "
            "infinite. `z` comes from the confidence level below, `p=0.5` "
            "is the most conservative assumption (the true agreement rate "
            "isn't known ahead of time), and `e` is the target margin of "
            "error. A **larger target** narrows the margin of error — the "
            "measured agreement rate is more likely to reflect the "
            "pipeline's true accuracy — at the cost of more reviewer time. "
            "A **smaller target** is faster to complete but the measured "
            "rate could be off by a wider margin — you could clear (or "
            "fail) the 90% agreement bar by sampling luck alone."
        )
        confidence = st.selectbox(
            "Confidence level",
            _CONFIDENCE_LEVELS,
            index=_CONFIDENCE_LEVELS.index(0.90),
            format_func=lambda c: f"{c:.0%}",
            key="review_confidence",
        )
        recommended_margin = (
            st.number_input(
                "Recommended-target margin of error (%)",
                min_value=1.0,
                max_value=50.0,
                value=8.0,
                step=1.0,
                key="review_recommended_margin_pct",
            )
            / 100
        )

    recommended_target = recommended_sample_size(
        population, confidence, recommended_margin
    )
    clamped_recommendation = min(max(recommended_target, 1), population)

    # st.number_input only honours `value=` the very first time a given
    # widget key exists — on every later rerun it keeps whatever the
    # widget already holds, `value=` or not. So changing confidence or
    # the recommended-target margin above wouldn't otherwise move the
    # target field at all; it would just make the help text/delta below
    # disagree with what's shown. Track the (confidence, margin) this
    # target was last computed from, and when either changes, write the
    # new recommendation into session state ourselves before the widget
    # reads it — the one way Streamlit lets you override a widget's
    # value after its first render.
    target_key = f"review_target_{country_iso}"
    margin_basis = (confidence, round(recommended_margin, 4))
    if (
        target_key not in st.session_state
        or st.session_state.get("review_target_margin_basis") != margin_basis
    ):
        st.session_state[target_key] = clamped_recommendation
        st.session_state["review_target_margin_basis"] = margin_basis

    target = st.number_input(
        "Review target for this region",
        min_value=1,
        max_value=population,
        step=10,
        key=target_key,
        help=(
            f"Statistically recommended: {recommended_target} reviews "
            f"(±{recommended_margin:.0%} margin of error at "
            f"{confidence:.0%} confidence, over a pool of {population})."
        ),
    )
    implied_margin = margin_of_error_for_sample_size(population, target, confidence)
    delta_pct = (implied_margin - recommended_margin) * 100
    st.metric(
        "Margin of error at this target",
        f"±{implied_margin:.1%}",
        delta=f"{delta_pct:+.1f} pts vs. recommended ({recommended_target} reviews)",
        delta_color="inverse",
    )
    if reviewed_count >= target:
        rate = summary["agreement_rate"] if summary else None
        rate_text = f"{rate:.0%}" if rate is not None else "n/a"
        if rate is not None and rate >= 0.90:
            st.success(
                f"{reviewed_count} of {target} reviewed at {rate_text} "
                f"agreement — target reached (margin of error "
                f"±{implied_margin:.1%})."
            )
        else:
            st.warning(
                f"{reviewed_count} of {target} reviewed at {rate_text} "
                "agreement — target reached, but below the 90% agreement "
                f"bar (margin of error ±{implied_margin:.1%})."
            )

if st.button("Load jobs") or st.session_state.review_loaded_region != country_iso:
    # Mark this region "attempted" up front, win or lose — otherwise a
    # failed auto-load (region just changed, API briefly down) never
    # clears the mismatch with `country_iso`, and every later rerun
    # (even one from an unrelated widget, e.g. typing a name) retries
    # the same failing fetch instead of waiting for an explicit click.
    st.session_state.review_loaded_region = country_iso
    try:
        response = httpx.get(
            f"{_settings.api_base_url}/classification/jobs-to-review",
            params=_region_params(limit=max(target, 1)),
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        st.session_state.review_jobs = payload["jobs"]
        st.session_state.review_index = 0
        st.session_state.review_submitted_count = 0
        # Snapshot the reviewed count now, so the progress bar reflects
        # the whole DB-backed pool rather than resetting to 0 every
        # time this page is closed and reopened.
        st.session_state.review_baseline_count = reviewed_count
    except httpx.HTTPError as exc:
        st.error(f"Failed to load jobs: {exc}")

jobs = st.session_state.review_jobs
index = st.session_state.review_index
baseline = st.session_state.review_baseline_count

if not jobs:
    st.success("No jobs need reviewing right now.")
elif index >= len(jobs):
    st.success(
        f"Done — reviewed all {len(jobs)} loaded jobs. Click 'Load jobs' for more."
    )
else:
    job = jobs[index]
    # Position only advances on an actual saved review (not on Skip),
    # and is capped at `target` — the reviewer can keep going past it,
    # and st.progress rejects a fraction above 1.0.
    position = baseline + st.session_state.review_submitted_count + 1
    denominator = max(target, position)
    st.progress(min(position / denominator, 1.0), text=f"Job {position} of {target}")

    st.subheader(job["title_for_display"] or job["title_raw"] or "(no title)")
    meta_cols = st.columns(5)
    meta_cols[0].write(f"Company: {job['company'] or '(none)'}")
    location_country = job["country_iso"] or "unresolved"
    if job["region"]:
        location_country = f"{location_country}, {job['region']}"
    meta_cols[1].write(f"Location: {job['location'] or '(none)'} ({location_country})")
    meta_cols[2].write(f"Pipeline method: {job['category_method']}")
    meta_cols[3].write(f"Confidence: {job['category_confidence']:.2f}")
    meta_cols[4].write(f"Job {index + 1} of {len(jobs)} in this batch")

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
            st.session_state.review_submitted_count += 1
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
