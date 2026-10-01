"""Scoring preferences (PLAN.md Step 15) — the stage-1 hard-filter settings
that gate the scoring funnel before anything expensive runs (vector
similarity, reranking, LLM fit).

Needs sign-in (PLAN.md Step 22a), not yet built — see the banner below.
"""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings
from core.ui.theme import apply_theme

_USER_GUIDE = """
#### What this page is for

These are Stage 1 of the scoring funnel (PLAN.md Step 15) — the cheap, hard
filters that decide whether a job is even considered before anything
expensive runs (vector similarity, the cross-encoder rerank, skill coverage,
the LLM fit re-rank). A job that fails any filter here never reaches those
later stages, and any score it previously earned is cleared.

Leaving a field at its default ("blank", "No minimum/maximum", or `0`) means
**no filter on that dimension** — it is never treated as "exclude
everything" or "exclude nothing" by assumption. In particular, a job whose
IR35 status is `unknown` is never auto-excluded, even if you exclude other
statuses.

#### Field notes

- **Preferred locations** — a job's `location` must match one of these
  exactly (comma-separated). Leaving this blank applies no location filter.
- **Remote work** — `required` keeps only jobs recognised as remote;
  `excluded` drops them; `preferred`/`no_preference` apply no filter here
  (a genuine preference for remote work, as opposed to a hard requirement,
  is a later-stage concern, not built yet).
- **Contract types** / **Excluded IR35 statuses** — jobs are matched against
  a job's own `engagement_type`/`ir35_status`.
- **Seniority band** — inclusive on both ends, ordered junior < mid <
  senior < lead < principal. A job with no seniority classification yet
  always passes (there's nothing to compare).
- **Minimum annual salary** — compared against a job's annualised rate,
  **GBP only**: a job priced in another currency can't be safely compared to
  a GBP floor (rates are never currency-converted), so it's filtered out on
  this dimension if a floor is set. Applies to any engagement type.
- **Minimum day rate** — same GBP-only rule, but this filter only applies to
  **non-permanent** jobs (contract, FTC, interim, unknown). A permanent
  salary's converted day-rate-equivalent isn't how permanent pay is judged,
  so permanent jobs are never excluded by this field.
- **Maximum posting age** — a job with no posting date at all is filtered
  out when this is set, since it can't be confirmed to be within the window.

#### After you change something

Preferences take effect the next time you run `score-filter-jobs` for your
user. A job that newly fails a filter has every downstream score cleared
immediately; nothing here is applied retroactively to a job that already
passed and scored under the old preferences until that command runs again.
"""

st.set_page_config(page_title="Scoring Preferences", layout="wide")
apply_theme()
st.title("Scoring Preferences")
with st.expander("User Guide", expanded=False):
    st.markdown(_USER_GUIDE)

st.info(
    "This page needs sign-in (PLAN.md Step 22a), not yet built — it will "
    "501 until then. Preferences can be set directly via SQL or a future "
    "admin path in the meantime."
)

_settings = get_settings()

_SENIORITY_BANDS = ["junior", "mid", "senior", "lead", "principal"]
_NO_MINIMUM = "No minimum"
_NO_MAXIMUM = "No maximum"
_REMOTE_OK_OPTIONS = ["required", "preferred", "no_preference", "excluded"]
_CONTRACT_TYPE_OPTIONS = ["permanent", "contract", "ftc", "interim"]
_IR35_STATUS_OPTIONS = [
    "inside",
    "outside",
    "not_applicable",
    "undetermined",
    "unknown",
]


def _error_message(exc: httpx.HTTPStatusError) -> str:
    """Build a readable message from an HTTP error response.

    Args:
        exc: The status error raised for the response.

    Returns:
        The API's JSON `detail`, or the exception text for a non-JSON body.
    """
    try:
        detail = exc.response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else str(exc)


def _get_preferences() -> dict | None:
    """Fetch the current scoring preferences.

    Returns:
        The parsed `GET /scoring/preferences` response body, or None if the
        request failed (the error is shown on the page).
    """
    try:
        response = httpx.get(
            f"{_settings.api_base_url}/scoring/preferences", timeout=10.0
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        st.error(_error_message(exc))
    except httpx.HTTPError as exc:
        st.error(f"Failed to load preferences: {exc}")
    return None


def _put_preferences(payload: dict) -> bool:
    """PUT the edited scoring preferences.

    Args:
        payload: The full preference body to save.

    Returns:
        True if the save succeeded; False if it failed, in which case the
        error is shown on the page instead of raised.
    """
    try:
        response = httpx.put(
            f"{_settings.api_base_url}/scoring/preferences", json=payload, timeout=10.0
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        st.error(_error_message(exc))
        return False
    except httpx.HTTPError as exc:
        st.error(f"Save failed: {exc}")
        return False
    return True


current = _get_preferences() or {
    "preferred_locations": [],
    "remote_ok": "no_preference",
    "contract_types": [],
    "excluded_ir35_statuses": [],
    "min_seniority_band": None,
    "max_seniority_band": None,
    "min_salary_annual": None,
    "min_rate_daily": None,
    "max_posting_age_days": None,
}

locations_text = st.text_input(
    "Preferred locations (comma-separated; blank means no filter)",
    value=", ".join(current["preferred_locations"]),
)

remote_ok = st.selectbox(
    "Remote work",
    _REMOTE_OK_OPTIONS,
    index=_REMOTE_OK_OPTIONS.index(current["remote_ok"]),
)

contract_types = st.multiselect(
    "Contract types (blank means no filter)",
    _CONTRACT_TYPE_OPTIONS,
    default=current["contract_types"],
)

excluded_ir35_statuses = st.multiselect(
    "Excluded IR35 statuses (blank excludes nothing)",
    _IR35_STATUS_OPTIONS,
    default=current["excluded_ir35_statuses"],
)

min_band_options = [_NO_MINIMUM, *_SENIORITY_BANDS]
min_seniority_band = st.selectbox(
    "Minimum seniority band",
    min_band_options,
    index=(
        min_band_options.index(current["min_seniority_band"])
        if current["min_seniority_band"] in _SENIORITY_BANDS
        else 0
    ),
)

max_band_options = [_NO_MAXIMUM, *_SENIORITY_BANDS]
max_seniority_band = st.selectbox(
    "Maximum seniority band",
    max_band_options,
    index=(
        max_band_options.index(current["max_seniority_band"])
        if current["max_seniority_band"] in _SENIORITY_BANDS
        else 0
    ),
)

min_salary_annual = st.number_input(
    "Minimum annual salary (0 means no filter)",
    min_value=0,
    value=int(current["min_salary_annual"] or 0),
    step=1000,
)

min_rate_daily = st.number_input(
    "Minimum day rate (0 means no filter)",
    min_value=0,
    value=int(current["min_rate_daily"] or 0),
    step=50,
)
st.caption(
    "Applies to non-permanent jobs only — permanent roles are never excluded by this."
)

max_posting_age_days = st.number_input(
    "Maximum posting age, in days (0 means no filter)",
    min_value=0,
    value=int(current["max_posting_age_days"] or 0),
    step=1,
)

if st.button("Save"):
    payload = {
        "preferred_locations": [
            loc.strip() for loc in locations_text.split(",") if loc.strip()
        ],
        "remote_ok": remote_ok,
        "contract_types": contract_types,
        "excluded_ir35_statuses": excluded_ir35_statuses,
        "min_seniority_band": (
            min_seniority_band if min_seniority_band != _NO_MINIMUM else None
        ),
        "max_seniority_band": (
            max_seniority_band if max_seniority_band != _NO_MAXIMUM else None
        ),
        "min_salary_annual": min_salary_annual or None,
        "min_rate_daily": min_rate_daily or None,
        "max_posting_age_days": max_posting_age_days or None,
    }
    if _put_preferences(payload):
        st.success("Preferences saved.")
        st.rerun()
