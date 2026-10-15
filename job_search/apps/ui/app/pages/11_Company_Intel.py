"""Company Intel - what the system knows about the company behind a job
(PLAN.md Step 19a): the resolved company, its researched profile, its top
competitors and a per-job competitive angle. Intel is shared and versioned;
the angle is analysis, not fact."""

from __future__ import annotations

from datetime import datetime

import httpx
import streamlit as st

from core.company_intel.schema import SNAPSHOT_LABELS
from core.settings import get_settings
from core.ui.theme import apply_theme

st.set_page_config(page_title="Company Intel", layout="wide")
apply_theme()
st.title("Company Intel")

with st.expander("User manual"):
    st.markdown(
        """
Pick a job. **Research** looks up the company behind it - its own website, a
profile with sources, its top competitors - and stores it; looking at the same
company again just shows what is saved: nothing is researched again unless
you click Refresh. Research and Refresh call Claude and cost about 8-10 cents
each, so they ask you to confirm first; viewing a company is free. If another
job at the same company was researched, you see that research here and can write
this job's competitive angle for about a cent. When a job is posted by an agency,
the page needs the **end client**: open *Correct the
company*, type it (and its website) and save.

Facts show the page they came from. **Competitors** marked *model knowledge -
unverified* come from the model's general knowledge. The **competitive angle**
is analysis written for this job - not verified fact.
"""
    )

_base = get_settings().api_base_url
_MD_META = set("\\`*_{}[]()#+-.!|<>~$:&")
# Measured on real runs (resolution + extraction + snapshot + angle, Claude).
_COST_NOTE = {
    "first": "about $0.10",
    "refresh": "about $0.08",
}
_FACT_FIELDS = (
    ("business_model", "Business model"),
    ("size_signals", "Size signals"),
    ("tech_stack", "Tech stack"),
    ("recent_announcements", "Recent announcements"),
    ("notes", "Notes"),
)


def _plain(text: object) -> str:
    """Escape text so Streamlit renders it literally, never as markdown.

    Args:
        text: Untrusted text (anything `str()`-able).

    Returns:
        The text with every markdown metacharacter backslash-escaped.
    """
    return "".join(f"\\{ch}" if ch in _MD_META else ch for ch in str(text))


def _date(value: object) -> str:
    """Format an API timestamp as a short date.

    Args:
        value: An ISO-8601 string.

    Returns:
        e.g. `14 Oct 2026`; the input as text when it cannot be parsed.
    """
    try:
        return datetime.fromisoformat(str(value)).strftime("%d %b %Y")
    except ValueError:
        return str(value)


def _get(path: str) -> httpx.Response:
    """GET from the API.

    Args:
        path: The path, starting with `/`.

    Returns:
        The response.
    """
    return httpx.get(f"{_base}{path}", timeout=15.0)


def _post(path: str, json: dict) -> httpx.Response:
    """POST to the API (research can take minutes).

    Args:
        path: The path, starting with `/`.
        json: The JSON body.

    Returns:
        The response.
    """
    return httpx.post(f"{_base}{path}", json=json, timeout=600.0)


def _put(path: str, json: dict) -> httpx.Response:
    """PUT to the API.

    Args:
        path: The path, starting with `/`.
        json: The JSON body.

    Returns:
        The response.
    """
    return httpx.put(f"{_base}{path}", json=json, timeout=30.0)


def _load_view(job_id: str, version: int | None = None) -> dict:
    """Load a job's intel view, stopping the page on an API error.

    Args:
        job_id: The job.
        version: A specific intel version, or None for the latest.

    Returns:
        The `GET /intel/jobs/{id}` answer.
    """
    path = f"/intel/jobs/{job_id}" + (f"?version={version}" if version else "")
    try:
        response = _get(path)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        st.error(_plain(f"Failed to load the intel: {exc}"))
        st.stop()


try:
    response = _get("/tailoring/candidates")
    response.raise_for_status()
    candidates = response.json()
except (httpx.HTTPError, ValueError) as exc:
    st.error(_plain(f"Failed to load your jobs: {exc}"))
    st.stop()

if not candidates:
    st.info(
        "No scored jobs yet - run the scoring stages from the Pipeline Dashboard first."
    )
    st.stop()

labels = {
    c["job_group_id"]: f"{c['title_for_display'] or '(no title)'} - "
    f"{c['company'] or 'unknown company'}"
    for c in candidates
}
# A link from a saved job spec (`?job=<job_group_id>`) opens on that job.
_linked_job = st.query_params.get("job")
if _linked_job in labels and "intel_linked_job" not in st.session_state:
    st.session_state["intel_linked_job"] = _linked_job
    st.session_state["intel-job"] = _linked_job
selected = st.selectbox(
    "Job",
    options=list(labels),
    format_func=lambda job_id: labels[job_id],
    key="intel-job",
)

_last = st.session_state.pop("intel-last-message", None)
if _last:
    (st.success if _last[0] in ("refreshed", "reused") else st.warning)(
        _plain(_last[1])
    )

view = _load_view(selected)
versions = view["versions"]
if len(versions) > 1:
    dates = {v["version"]: _date(v["researched_at"]) for v in versions}
    chosen = st.selectbox(
        "Version",
        options=list(dates),
        format_func=lambda n: f"v{n} - {dates[n]}",
        key=f"intel-version-{selected}",
    )
    if view["intel"] is None or chosen != view["intel"]["version"]:
        view = _load_view(selected, chosen)
resolution, intel, angle = view["resolution"], view["intel"], view["angle"]

st.subheader("Company")
if resolution is not None:
    st.markdown(
        "\n".join(
            [
                f"- **Posted by:** {_plain(resolution['posted_company'] or 'unknown')}",
                f"- **Poster is:** {_plain(resolution['poster_kind'])}",
                "- **Researching:** "
                f"{_plain(resolution['end_client'] or 'not known yet')}",
                "- **Website:** "
                f"{_plain(resolution['website_url'] or 'not known yet')}",
                f"- **Set by:** {_plain(resolution['method'])}",
            ]
        )
    )
    if resolution["end_client"] is None:
        st.warning(
            "This posting does not name the end client. Enter the end client "
            "and its website below, then research it."
        )
    elif not resolution["website_url"]:
        st.warning(
            "The company's website is not known. Enter it below - it is "
            "needed to research the company."
        )
# Research cannot do anything until the company and its website are known, so
# the form opens and the button waits, instead of looping on the same message.
needs_input = resolution is not None and (
    resolution["end_client"] is None or not resolution["website_url"]
)
if intel is None:
    if not needs_input:
        st.info("No intel yet - click Research.")
elif resolution is None:
    st.info(
        f"Showing the saved research for {_plain(intel['display_name'])}, found "
        "by the company named on this posting. Nothing has been researched for "
        "this job itself yet, and nothing runs unless you ask."
    )


def _company_form() -> None:
    """Show the end client / website form and save it through the API."""
    end_client = st.text_input("End client", key="intel-end-client")
    website = st.text_input("Website", key="intel-website")
    if st.button("Save", key="intel-save-resolution"):
        body = {"end_client": end_client or None, "website_url": website or None}
        try:
            saved = _put(f"/intel/jobs/{selected}/resolution", body)
            saved.raise_for_status()
        except httpx.HTTPError as exc:
            st.error(_plain(f"Could not save: {exc}"))
        else:
            st.rerun()


if needs_input:
    # Shown on the page itself, not in a collapsible: Streamlit remembers an
    # expander the user collapsed earlier and would keep it shut.
    st.subheader("Which company is this?")
    _company_form()
else:
    with st.expander("Correct the company"):
        _company_form()

label, button_key = (
    ("Refresh", "intel-refresh") if intel else ("Research", "intel-research")
)
if st.button(
    label,
    key=button_key,
    disabled=needs_input,
    help="Enter the end client and its website first." if needs_input else None,
):
    st.session_state["intel-confirming"] = selected
if st.session_state.get("intel-confirming") == selected:
    cost = _COST_NOTE["refresh" if intel else "first"]
    st.warning(
        f"{label} calls Claude and costs money: {cost} for this company, and it "
        "takes a minute or two. Nothing is spent unless you confirm."
    )
    confirm_col, cancel_col = st.columns(2)
    if cancel_col.button("Cancel", key="intel-cancel"):
        st.session_state.pop("intel-confirming", None)
        st.rerun()
    if confirm_col.button(f"Confirm {label.lower()}", key="intel-confirm"):
        st.session_state.pop("intel-confirming", None)
        with st.spinner("Researching - this can take a few minutes"):
            try:
                answer = _post(
                    f"/intel/jobs/{selected}/research", {"force": bool(intel)}
                )
                answer.raise_for_status()
                outcome = answer.json()
            except (httpx.HTTPError, ValueError) as exc:
                st.error(_plain(f"Research failed: {exc}"))
                st.stop()
        st.session_state["intel-last-message"] = (
            outcome["outcome"],
            outcome["message"],
        )
        st.rerun()

if intel is not None:
    badge = "Fresh" if intel["fresh"] else "Stale"
    st.markdown(
        f"**{badge}** - v{intel['version']}, "
        f"researched {_date(intel['researched_at'])}, "
        f"status {_plain(intel['status'])}"
    )
    if not intel["fresh"]:
        st.caption(
            f"This research is older than {intel['refresh_after_days']} days. It "
            "is still used as it is; nothing is re-researched by itself. Use "
            "Refresh if you want it updated."
        )
    if intel["status"] == "no_content":
        st.info("The company's pages gave no usable facts.")
    snapshot = intel["content"].get("snapshot") or []
    if snapshot:
        st.subheader("Company snapshot")
        st.dataframe(
            [
                {
                    "Metric": SNAPSHOT_LABELS.get(row["key"], row["key"]),
                    "Value": row["value"],
                    "As of": row.get("as_of") or "",
                }
                for row in snapshot
            ],
            hide_index=True,
            use_container_width=True,
        )
        st.caption(
            "From the model's general knowledge, not looked up: approximate, "
            "possibly out of date, and unverified."
        )
    for field, title in _FACT_FIELDS:
        facts = intel["content"].get(field) or []
        if not facts:
            continue
        st.markdown(f"**{title}**")
        for fact in facts:
            st.markdown(f"- {_plain(fact['text'])}")
            st.caption(_plain(fact["source_url"]))
    competitors = intel["content"].get("competitors") or []
    if competitors:
        st.subheader("Competitors")
        for competitor in competitors:
            why = f" - {_plain(competitor['why'])}" if competitor.get("why") else ""
            st.markdown(f"**{_plain(competitor['name'])}**{why}")
            if competitor["basis"] == "site":
                st.caption(f"site: {_plain(competitor['source_url'])}")
            else:
                st.caption("model knowledge — unverified")

if angle is not None:
    st.subheader("Competitive angle")
    st.caption("analysis — not verified")
    content = angle["content"]
    if content.get("transformation"):
        st.markdown(_plain(content["transformation"]))
    for item in content.get("advantages") or []:
        who = _plain(item["competitor"]) if item.get("competitor") else "the field"
        st.markdown(f"- vs {who}: {_plain(item['point'])}")
    for caveat in content.get("caveats") or []:
        st.caption(_plain(caveat))
    if intel is not None and angle["intel_version"] != intel["version"]:
        st.caption(f"Written from intel v{angle['intel_version']}.")

if intel is not None and angle is None:
    st.subheader("Competitive angle")
    st.caption("There is no competitive angle for this job yet.")
    if st.button("Write the competitive angle for this job", key="intel-angle"):
        st.session_state["intel-angle-confirming"] = selected
    if st.session_state.get("intel-angle-confirming") == selected:
        st.warning(
            "Writing the angle calls Claude once and costs about $0.01. The "
            "company is not researched again. Nothing is spent unless you confirm."
        )
        angle_confirm, angle_cancel = st.columns(2)
        if angle_cancel.button("Cancel", key="intel-angle-cancel"):
            st.session_state.pop("intel-angle-confirming", None)
            st.rerun()
        if angle_confirm.button("Confirm angle", key="intel-angle-confirm"):
            st.session_state.pop("intel-angle-confirming", None)
            with st.spinner("Writing the angle"):
                try:
                    written = _post(f"/intel/jobs/{selected}/angle", {"force": False})
                    written.raise_for_status()
                except httpx.HTTPError as exc:
                    st.error(_plain(f"Could not write the angle: {exc}"))
                    st.stop()
            st.session_state["intel-last-message"] = (
                "refreshed",
                "The competitive angle was written and saved.",
            )
            st.rerun()
