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


def _lookup_curve_point(curve: list[dict], threshold: float) -> dict:
    """Find the curve point whose precision/recall apply at `threshold`.

    The curve only has one point per distinct historical blended_score,
    sorted descending by threshold — it has no point at an arbitrary
    chosen threshold. predicted_match_count (and therefore precision/
    recall) is a step function of threshold, so the closest point at or
    above the chosen threshold is the correct one: any pair scoring at
    or above that point's threshold would also score at or above the
    chosen threshold.

    Args:
        curve: The full precision-recall curve, sorted descending by
            threshold (as returned by GET /dedup/calibration).
        threshold: The user's chosen auto-match threshold.

    Returns:
        The chosen curve point. If `threshold` exceeds every historical
        score, no curve point applies at that threshold — no labeled
        pair scores that high, so predicted_match_count would be 0 and
        precision is undefined per `compute_precision_recall_curve`'s
        own rule (`precision = true_positives / len(predicted) if
        predicted else None`). Returns a synthetic point signaling
        that: `{"precision": None, "recall": None,
        "predicted_match_count": 0}`.
    """
    candidates = [point for point in curve if point["threshold"] >= threshold]
    if not candidates:
        return {"precision": None, "recall": None, "predicted_match_count": 0}
    return min(candidates, key=lambda point: point["threshold"])


st.set_page_config(page_title="Dedup Calibration", layout="wide")
st.title("Dedup Calibration")

with st.expander("User manual"):
    st.markdown(
        """
The **x-axis of the chart is "threshold"** — a candidate cutoff for the
blended similarity score. Every point on it asks: *"if I called every
pair with a score at or above this number an auto-match, what would
precision and recall be?"* The two lines are **precision** (light) and
**recall** (dark) at each of those candidate cutoffs.

**Precision** asks: *of the pairs this threshold calls a match, how
many actually are one?* It's calculated as

```
precision = (labeled "match" pairs scoring ≥ threshold)
            ────────────────────────────────────────────
            (all pairs scoring ≥ threshold, match or not)
```

Concretely: say this threshold calls 10 pairs "a match," and 9 of
those 10 turn out to really be the same job — precision is 9/10 =
**0.90**. Raising the threshold means fewer pairs clear the bar at
all, but the ones that still do are the ones the model is most
confident about — so as you raise the threshold, precision usually
climbs toward 1.0.

**Recall** asks the opposite question: *of every pair you've actually
labeled a match, how many did this threshold catch?* It's calculated
as

```
recall = (labeled "match" pairs scoring ≥ threshold)
         ──────────────────────────────────────────────
         (all pairs you labeled "match", regardless of score)
```

Concretely: say you've labeled 20 pairs as real matches overall, and
at this threshold only 5 of those 20 score high enough to clear it —
recall is 5/20 = **0.25**. The other 15 real matches scored below
this threshold and are missed entirely. Raising the threshold makes
this worse, not better: a stricter bar catches fewer of the real
matches, so recall usually falls toward 0 as you raise the threshold.

Precision and recall pull in opposite directions as you move the
threshold — that's the whole reason there's a curve here instead of
one obvious number.

Your **Auto-match threshold** number below is literally one x-value on
that chart — the "Measured precision/recall" boxes are just reading
the two lines' heights at that point. For example: at a threshold of
**0.80**, the chart might show precision **1.000** and recall
**0.250**. That means every pair scoring ≥ 0.80 happens to be a real
match (precision = 1.0), but that's a small, strict slice — only about
a quarter of the actual "match"-labeled pairs score that high
(recall = 0.25). The rest of the real matches, scoring below 0.80,
would not get auto-matched at that threshold.

The **Auto-reject threshold** is *not* read from this chart at all —
there's no plotted line for "reject precision." It's a separate number
you set independently: anything scoring at or below it is confidently
treated as "not the same job," no auto-match consideration needed. It
sits below the noisy middle of the curve, by design.

**Is a high-precision, low-recall pick (like 0.80 / 1.000 precision /
0.250 recall above) a good choice?** PLAN.md's target is precision
above 0.95 at the auto-match threshold — a value like that comfortably
clears the bar. The recall tradeoff is intentional and safe: everything
that doesn't clear the auto-match threshold falls into the "middle
band" between the two thresholds, which goes to the **Dedup Review
Queue** for a human decision rather than being silently auto-merged.
This is deliberate: a missed auto-match just means "you review it,"
while a wrong auto-match silently loses a real job from the pipeline
forever. Lowering the auto-match threshold trades some of that safety
margin for a higher auto-match recall — precision will drop but more
pairs get merged automatically without a human in the loop.
"""
    )

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
    st.caption(
        "Measured precision/recall are looked up from the curve above at "
        "the chosen auto-match threshold — they are never typed by hand, "
        "so what gets recorded always matches a real measurement. Adjust "
        "the threshold below and the preview updates immediately; nothing "
        "is saved until you click Save thresholds."
    )

    col1, col2 = st.columns(2)
    with col1:
        auto_match_threshold = st.number_input(
            "Auto-match threshold", min_value=0.0, max_value=1.0, value=0.9
        )
    with col2:
        auto_reject_threshold = st.number_input(
            "Auto-reject threshold", min_value=0.0, max_value=1.0, value=0.5
        )

    selected_point = _lookup_curve_point(curve, auto_match_threshold)
    measured_precision = selected_point["precision"]
    measured_recall = selected_point["recall"]

    metric_col1, metric_col2 = st.columns(2)
    metric_col1.metric(
        "Measured precision at that threshold",
        f"{measured_precision:.3f}" if measured_precision is not None else "n/a",
    )
    metric_col2.metric(
        "Measured recall at that threshold",
        f"{measured_recall:.3f}" if measured_recall is not None else "n/a",
    )

    calibrated_by = st.text_input("Your name")
    submitted = st.button("Save thresholds")

    if submitted:
        if measured_precision is None or measured_recall is None:
            st.error(
                "Precision/recall are undefined at this threshold (no "
                "predicted matches, or no true matches in the label set) "
                "— choose a different auto-match threshold before saving."
            )
        else:
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
