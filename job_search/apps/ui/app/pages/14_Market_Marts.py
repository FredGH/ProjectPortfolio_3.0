"""Market Marts - a read-only look at the Step 21a market intelligence marts
(PLAN.md Step 21a). It shows what dbt last built; it never builds anything.
The full Step 21 dashboard will replace it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import streamlit as st

from core.settings import get_settings
from core.ui.theme import apply_theme

st.set_page_config(page_title="Market Marts", layout="wide")
apply_theme()
st.title("Market Marts")

with st.expander("User manual"):
    st.markdown(
        """
A **read-only preview** of the market marts that dbt builds from your
collected jobs. It never changes anything: if the numbers are old or missing,
rebuild the marts (the command is shown at the top).

Read every figure with its denominator. **Salary** shows how many postings
there were (*n*), how many disclosed a figure and the *disclosure rate*; a
median appears only with at least 5 disclosed figures. **Change** columns
(week-on-week, rank change) stay empty - *insufficient history* - until there
are comparable periods on the same source panel and query-matrix version, so a
new source or keyword never looks like a hiring boom. Only targeted-channel
API postings count; manual entries never do.
"""
    )

_base = get_settings().api_base_url
_MD_META = set("\\`*_{}[]()#+-.!|<>~$:&")
_STALE_AFTER = timedelta(days=7)
_NO_HISTORY = "insufficient history"


def _plain(text: object) -> str:
    """Escape text so Streamlit renders it literally, never as markdown.

    Args:
        text: Untrusted text (anything `str()`-able).

    Returns:
        The text with every markdown metacharacter backslash-escaped.
    """
    return "".join(f"\\{ch}" if ch in _MD_META else ch for ch in str(text))


def _get(path: str, **params: object) -> httpx.Response:
    """GET from the API.

    Args:
        path: The path, starting with `/`.
        **params: Query parameters (None values are dropped).

    Returns:
        The response.
    """
    clean = {k: v for k, v in params.items() if v is not None}
    return httpx.get(f"{_base}{path}", params=clean, timeout=30.0)


def _load(path: str, **params: object) -> list | dict | None:
    """Load an endpoint, showing an error and returning None on failure.

    Args:
        path: The path, starting with `/`.
        **params: Query parameters.

    Returns:
        The decoded JSON, or None after showing an error.
    """
    try:
        response = _get(path, **params)
    except httpx.HTTPError as exc:
        st.error(_plain(f"Failed to load {path}: {exc}"))
        return None
    if response.status_code != 200:
        try:
            detail = response.json().get("detail", response.status_code)
        except (ValueError, AttributeError):
            detail = response.status_code
        st.error(_plain(detail))
        return None
    return response.json()


def _pct(value: object) -> str:
    """Format a share as a percentage.

    Args:
        value: A fraction, or None.

    Returns:
        e.g. `31%`, or an empty string for None.
    """
    return "" if value is None else f"{float(value) * 100:.0f}%"


def _gap(value: object) -> object:
    """Show a missing change metric as 'insufficient history'.

    Args:
        value: A change value, or None.

    Returns:
        The value, or the placeholder text for None.
    """
    return _NO_HISTORY if value is None else str(value)


overview = _load("/market/overview")
if not isinstance(overview, dict):
    st.stop()

built_at = datetime.fromisoformat(overview["built_at"])
age = datetime.now(UTC) - built_at
st.caption(
    f"Built {built_at:%d %b %Y %H:%M} UTC - {overview['n_jobs']} jobs - source "
    f"panel: {_plain(overview['panel_sources'])} - query matrix "
    f"{_plain(overview['matrix_version'])}."
)
if age > _STALE_AFTER:
    st.warning(
        f"These marts were built {age.days} days ago and may be stale. Rebuild "
        f"them, then reload this page:  \n`{overview['rebuild_command']}`"
    )
else:
    st.caption(f"Rebuild with: `{overview['rebuild_command']}`")
if not overview.get("panel_complete", False):
    st.info(
        "The source panel is not complete for the earliest period (the sources "
        "began mid-period), so no change metric is shown yet: the figures below "
        "are a cross-section, not a trend."
    )
if overview.get("trend_break"):
    st.info("The query matrix changed during this history: trend lines break there.")

categories = overview["categories"]
if not categories:
    st.info("No market data yet.")
    st.stop()
category = st.selectbox(
    "Category", categories, format_func=lambda c: c.replace("_", " "), key="mm-category"
)

(
    salary_tab,
    skills_tab,
    premium_tab,
    stacks_tab,
    mix_tab,
    volume_tab,
    employers_tab,
    life_tab,
) = st.tabs(
    ["Salary", "Skills", "Premium", "Stacks", "Mix", "Volume", "Employers", "Lifecycle"]
)

with salary_tab:
    rows = _load("/market/salary", category=category)
    if rows:
        st.dataframe(
            [
                {
                    "Region": r["region"],
                    "Contract": r["contract_type"],
                    "Unit": r["unit"],
                    "Postings (n)": r["n_postings"],
                    "Disclosed (n)": r["n_disclosed"],
                    "Disclosure rate": _pct(r["disclosure_rate"]),
                    "Median": None if r["median"] is None else round(r["median"]),
                    "P25": None if r["p25"] is None else round(r["p25"]),
                    "P75": None if r["p75"] is None else round(r["p75"]),
                    "Min": r["min_advertised"],
                    "Max": r["max_advertised"],
                    "Month": r["period_start"],
                }
                for r in rows
            ],
            hide_index=True,
            use_container_width=True,
        )
        st.caption(
            "Permanent figures are GBP per year, contract figures GBP per day, "
            "never mixed. A blank median means fewer than 5 disclosed figures."
        )
    elif rows is not None:
        st.info("No permanent or contract postings in this category yet.")

with skills_tab:
    rows = _load("/market/skills", category=category, limit=25)
    if rows:
        st.dataframe(
            [
                {
                    "Skill": r["skill_label"],
                    "Postings": r["frequency"],
                    "Share of category": _pct(r["share"]),
                    "Must-have": _pct(r["must_have_ratio"]),
                    "Rank change (90 days)": _gap(r["rank_delta_90d"]),
                }
                for r in rows
            ],
            hide_index=True,
            use_container_width=True,
        )
    elif rows is not None:
        st.info("No skills extracted for this category yet.")

with premium_tab:
    rows = _load("/market/premium", category=category)
    if rows:
        st.dataframe(
            [
                {
                    "Skill": r["skill_label"],
                    "Contract": r["contract_type"],
                    "Unit": r["unit"],
                    "With skill (n)": r["n_with"],
                    "Without (n)": r["n_without"],
                    "Median with": (
                        None if r["median_with"] is None else round(r["median_with"])
                    ),
                    "Median without": (
                        None
                        if r["median_without"] is None
                        else round(r["median_without"])
                    ),
                    "Premium": None if r["premium"] is None else round(r["premium"]),
                }
                for r in rows
            ],
            hide_index=True,
            use_container_width=True,
        )
        st.caption(
            "A premium needs at least 5 disclosed salaries with AND without the skill."
        )
    elif rows is not None:
        st.info(
            "No premium can be published yet: no skill has 5 disclosed salaries "
            "on both sides. It fills as more postings disclose pay."
        )

with stacks_tab:
    rows = _load("/market/cooccurrence", category=category, limit=25)
    if rows:
        st.dataframe(
            [
                {
                    "Skill A": r["skill_a_label"],
                    "Skill B": r["skill_b_label"],
                    "Postings together": r["support"],
                    "Lift": round(r["lift"], 2),
                }
                for r in rows
            ],
            hide_index=True,
            use_container_width=True,
        )
        st.caption("Lift above 1 means the pair appears together more than chance.")
    elif rows is not None:
        st.info("No skill pair appears in at least 3 postings yet.")

with mix_tab:
    rows = _load("/market/composition", category=category)
    if rows:
        latest = rows[-1]
        st.markdown(
            f"**{_plain(latest['period_start'])}** - {latest['n_postings']} postings"
        )
        left, middle, right = st.columns(3)
        left.markdown("**Work model**")
        left.bar_chart(
            {
                "remote": latest["share_remote"],
                "hybrid": latest["share_hybrid"],
                "onsite": latest["share_onsite"],
                "unknown": latest["share_work_model_unknown"],
            }
        )
        middle.markdown("**Contract type**")
        middle.bar_chart(
            {
                "permanent": latest["share_permanent"],
                "contract": latest["share_contract"],
                "unknown": latest["share_contract_type_unknown"],
            }
        )
        right.markdown("**Seniority**")
        right.bar_chart(
            {
                "junior": latest["share_junior"],
                "mid": latest["share_mid"],
                "senior": latest["share_senior"],
                "lead": latest["share_lead"],
                "principal": latest["share_principal"],
                "unknown": latest["share_seniority_unknown"],
            }
        )
        st.caption(
            "Work model comes from keyword rules on the posting text; 'unknown' is "
            "a real share - many postings do not say."
        )

with volume_tab:
    rows = _load("/market/volume", category=category)
    if rows:
        st.dataframe(
            [
                {
                    "Week": r["period_start"],
                    "Postings": r["postings"],
                    "Change vs last week": _gap(r["wow_change"]),
                    "Panel complete": r["panel_complete"],
                    "Matrix": r["matrix_version"],
                }
                for r in rows
            ],
            hide_index=True,
            use_container_width=True,
        )
        st.caption(
            "Weeks are when we first saw each posting, not when it was posted. "
            "Treat a week as a trend point only when its panel is complete."
        )

with employers_tab:
    rows = _load("/market/employers", category=category, limit=25)
    if rows:
        st.dataframe(
            [
                {
                    "Employer": r["company_name"],
                    "Postings": r["postings"],
                    "Agency share": _pct(r["agency_share"]),
                    "First seen": r["first_seen_month"],
                }
                for r in rows
            ],
            hide_index=True,
            use_container_width=True,
        )

with life_tab:
    summary = _load("/market/lifecycle", category=category)
    if isinstance(summary, dict):
        first, second, third = st.columns(3)
        first.metric("Jobs", summary["n_jobs"])
        second.metric("Still live", summary["n_live"])
        third.metric("Gone", summary["n_gone"])
        if summary["median_days_live"] is None:
            st.info(
                "No posting has disappeared yet, so there is no time-to-fill "
                "figure: a live posting is not given a fake duration. It "
                f"appears as collection repeats ({_NO_HISTORY})."
            )
        else:
            st.metric(
                "Median days live (gone postings)",
                round(summary["median_days_live"], 1),
            )
