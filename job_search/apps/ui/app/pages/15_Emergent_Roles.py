"""Emergent Roles - candidate new job functions found in the discovery corpus,
for you to confirm or dismiss (PLAN.md Step 21b). Every name is a hypothesis
written by a model; nothing here is a finding until you say so. Below the
maturity threshold no novelty index is shown, only a labelled preview."""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings
from core.ui.theme import apply_theme

st.set_page_config(page_title="Emergent Roles", layout="wide")
apply_theme()
st.title("Emergent Roles")

with st.expander("User manual"):
    st.markdown(
        """
This page looks for job titles that are **forming outside your categories**: it
groups the canonical titles of the wide discovery corpus (not your targeted
searches), keeps the groups that sit far from every existing category, and asks
a model to name them. Treat every name as a **question, never an answer**: the
model will confidently name clusters that are artefacts of what was collected.

Guards: agency postings are removed first; title-inflation words ("ninja",
"rockstar") are dropped; a group needs enough **distinct non-agency employers**
before it is a candidate at all; and **you** confirm or dismiss each one with a
reason. Nothing is added to your categories - confirming only records your
verdict. The novelty index is **not shown until there is enough history**; until
then you see a labelled preview.

A **hybrid** is a group that sits closer to a blend of two categories (for
example 60% software engineer + 40% data engineer) than to either alone.
"""
    )

_base = get_settings().api_base_url
_MD_META = set("\\`*_{}[]()#+-.!|<>~$:&")
_BUILD = "build-emergent-titles -> cluster-emergent-roles -> name-emergent-roles"


def _plain(text: object) -> str:
    """Escape text so Streamlit renders it literally, never as markdown.

    Args:
        text: Untrusted text (anything `str()`-able).

    Returns:
        The text with every markdown metacharacter backslash-escaped.
    """
    return "".join(f"\\{ch}" if ch in _MD_META else ch for ch in str(text))


def _call(method: str, path: str, **kwargs: object) -> httpx.Response:
    """Call the API.

    Args:
        method: `GET` or `POST`.
        path: The path, starting with `/`.
        **kwargs: Extra httpx arguments.

    Returns:
        The response.
    """
    return httpx.request(method, f"{_base}{path}", timeout=60.0, **kwargs)  # type: ignore[arg-type]


def _detail(response: httpx.Response) -> str:
    """Read an API error message.

    Args:
        response: A non-2xx response.

    Returns:
        The `detail` field, or the status code when there is none.
    """
    try:
        return str(response.json().get("detail", response.status_code))
    except (ValueError, AttributeError):
        return str(response.status_code)


def _hybrid_text(hybrid: dict | None) -> str:
    """Describe a blend in words.

    Args:
        hybrid: The blend dict, or None.

    Returns:
        e.g. `60% software engineer + 40% data engineer`, or an empty string.
    """
    if not hybrid:
        return ""
    a = hybrid["category_a"].replace("_", " ")
    b = hybrid["category_b"].replace("_", " ")
    return f"{hybrid['weight_a'] * 100:.0f}% {a} + {hybrid['weight_b'] * 100:.0f}% {b}"


try:
    response = _call("GET", "/emergent/candidates")
except httpx.HTTPError as exc:
    st.error(_plain(f"Failed to load: {exc}"))
    st.stop()
if response.status_code != 200:
    st.error(_plain(_detail(response)))
    st.stop()
data = response.json()

if data["novelty_shown"]:
    st.success(
        f"The discovery corpus has enough history: {data['history_days']:.0f} days, "
        f"{data['periods']} weekly periods, {data['employers']} employers."
    )
else:
    reason = (
        f"missing: {_plain(data['missing'])}"
        if not data["mature"]
        else "the latest cluster run was made while the corpus was too young - "
        "run cluster-emergent-roles again"
    )
    st.warning(
        f"**{data['preview_label'].capitalize()}.** Not enough history for a novelty "
        f"index ({reason}). The groups below are a "
        f"cross-section of what was collected, not a trend. The employer floor is "
        f"{data['min_employers']} distinct non-agency employers; the corpus has "
        f"{data['employers']}."
    )
st.caption(
    f"{data['agency_jobs_excluded']} agency postings were excluded before scoring. "
    f"Pipeline (CLI): {_BUILD}."
)
run = data["run"]
if run is None:
    st.info("No cluster run yet. Run the pipeline commands above, then reload.")
    st.stop()
st.caption(
    f"Latest run: {run['n_titles']} canonical titles, {run['n_clusters']} clusters, "
    f"{run['n_noise']} titles left as noise."
)

_last = st.session_state.pop("em-last-message", None)
if _last:
    st.success(_plain(_last))

st.subheader("Candidates to review")
candidates = data["candidates"]
if not candidates:
    st.info(
        "No named candidates. A cluster becomes a candidate only if it sits outside "
        "every category AND has enough distinct non-agency employers; name them with "
        "`name-emergent-roles`."
    )
for candidate in candidates:
    cluster = candidate["cluster"]
    with st.container(border=True):
        st.markdown(f"### {_plain(candidate['name'])}")
        st.caption(
            f"hypothesis - {candidate['corroboration']} "
            "(no external check has been made)"
        )
        st.markdown(_plain(candidate["characterisation"]))
        left, middle, right = st.columns(3)
        left.metric("Distinct employers", cluster["n_employers"])
        middle.metric(
            "Closest category",
            (cluster["nearest_category"] or "none").replace("_", " "),
        )
        if cluster["novelty"] is None:
            right.metric("Novelty index", "not shown")
            right.caption(_plain(cluster["novelty_note"]))
        else:
            right.metric("Novelty index", f"{cluster['novelty']:.2f}")
            right.caption(_plain(cluster["novelty_note"]))
        hybrid = _hybrid_text(cluster["hybrid"])
        if hybrid:
            st.markdown(f"**Hybrid:** {_plain(hybrid)}")
        if candidate["skill_signature"]:
            st.markdown(
                "**Skill signature:** "
                + _plain(", ".join(candidate["skill_signature"]))
            )
        with st.expander(f"Titles in this group ({len(cluster['titles'])})"):
            for title in cluster["titles"]:
                st.markdown(f"- {_plain(title)}")
        if candidate["verdict"] != "pending":
            st.markdown(
                f"**Your verdict: {candidate['verdict']}** - "
                f"{_plain(candidate['rationale'] or '')}"
            )
            continue
        reason = st.text_area("Why?", key=f"em-reason-{candidate['id']}", height=70)
        confirm_col, dismiss_col = st.columns(2)
        for column, key, verdict in (
            (confirm_col, f"em-confirm-{candidate['id']}", "confirmed"),
            (dismiss_col, f"em-dismiss-{candidate['id']}", "dismissed"),
        ):
            if column.button(
                "Confirm" if verdict == "confirmed" else "Dismiss", key=key
            ):
                answer = _call(
                    "POST",
                    f"/emergent/candidates/{candidate['id']}/review",
                    json={"verdict": verdict, "rationale": reason},
                )
                if answer.status_code != 200:
                    st.error(_plain(_detail(answer)))
                else:
                    st.session_state["em-last-message"] = f"Recorded: {verdict}."
                    st.rerun()

with st.expander("All clusters in the latest run"):
    try:
        everything = _call("GET", "/emergent/clusters")
    except httpx.HTTPError as exc:
        st.error(_plain(f"Failed to load: {exc}"))
    else:
        rows = (
            everything.json().get("clusters", [])
            if everything.status_code == 200
            else []
        )
        st.dataframe(
            [
                {
                    "Titles": ", ".join(c["titles"][:4])
                    + (" ..." if len(c["titles"]) > 4 else ""),
                    "Employers": c["n_employers"],
                    "Closest category": c["nearest_category"],
                    "Cosine": (
                        None
                        if c["nearest_cosine"] is None
                        else round(c["nearest_cosine"], 2)
                    ),
                    "Candidate": c["is_candidate"],
                    "Why": c["candidate_note"],
                    "Hybrid": _hybrid_text(c["hybrid"]),
                }
                for c in rows
            ],
            hide_index=True,
            use_container_width=True,
        )
        if not data["mature"]:
            st.caption(data["preview_label"])
