"""Skill review (PLAN.md Step 14) — resolve skill strings that did not map to
ESCO, and verify the ones the embedding stage mapped automatically.

A resolution is remembered as an alias, so each string is fixed once and the
fix applies to every future CV and job description. Aliases are shared across
users (docs/tenancy.md: taxonomy is a shared zone).
"""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Skill Review", layout="wide")
st.title("Skill Review")
st.write(
    "Skills from job descriptions and your CV that could not be matched to the "
    "ESCO vocabulary, plus the matches the system made by similarity — check "
    "those, since a wrong match is otherwise invisible."
)

_API = get_settings().api_base_url


def _get(path: str, params: dict | None = None) -> list[dict]:
    """GET a list endpoint on the API.

    Args:
        path: The endpoint path.
        params: Query parameters.

    Returns:
        The parsed JSON list.

    Raises:
        httpx.HTTPError: If the request fails.
    """
    response = httpx.get(f"{_API}{path}", params=params, timeout=10.0)
    response.raise_for_status()
    return response.json()


def _post(path: str, payload: dict) -> bool:
    """POST an action to the API, showing any error on the page.

    Args:
        path: The endpoint path.
        payload: The JSON body.

    Returns:
        True if the action succeeded.
    """
    try:
        response = httpx.post(f"{_API}{path}", json=payload, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        st.error(exc.response.json().get("detail", str(exc)))
        return False
    except httpx.HTTPError as exc:
        st.error(f"Request failed: {exc}")
        return False
    return True


unmapped_tab, verify_tab = st.tabs(["Unmapped", "Embedding matches — verify"])

with unmapped_tab:
    try:
        unmapped = _get("/skills/review", {"limit": 25})
    except httpx.HTTPError as exc:
        st.error(f"Failed to load the review list: {exc}")
        unmapped = []
    st.caption(f"{len(unmapped)} shown, most-requested first")
    for item in unmapped:
        key = item["raw_norm"]
        with st.container(border=True):
            st.markdown(f"**{item['raw_example']}**")
            st.caption(
                f"In {item['jd_job_count']} job(s)"
                + (" · on your CV" if item["seen_in_cv"] else "")
            )
            if item["candidate_skill_id"]:
                suggestion = item["candidate_label"] or item["candidate_skill_id"]
                if st.button(
                    f"Accept suggestion: {suggestion} ({item['candidate_score']:.2f})",
                    key=f"accept-{key}",
                ):
                    if _post(
                        "/skills/review/resolve",
                        {"raw_norm": key, "skill_id": item["candidate_skill_id"]},
                    ):
                        st.rerun()
            query = st.text_input("Search ESCO / custom skills", key=f"q-{key}")
            if query:
                try:
                    options = _get("/skills/search", {"q": query, "limit": 10})
                except httpx.HTTPError as exc:
                    st.error(f"Search failed: {exc}")
                    options = []
                choices = {
                    f"{o['label']} ({o['source']})": o["skill_id"] for o in options
                }
                if choices:
                    picked = st.selectbox("Match", list(choices), key=f"sel-{key}")
                    if st.button("Map to selected", key=f"map-{key}"):
                        if _post(
                            "/skills/review/resolve",
                            {"raw_norm": key, "skill_id": choices[picked]},
                        ):
                            st.rerun()
                else:
                    st.caption("No matches.")
            custom_label = st.text_input(
                "Or create a custom skill", value=item["raw_example"], key=f"c-{key}"
            )
            left, right = st.columns(2)
            if left.button("Mark as custom skill", key=f"custom-{key}"):
                if _post(
                    "/skills/review/resolve",
                    {"raw_norm": key, "custom_label": custom_label},
                ):
                    st.rerun()
            if right.button("Dismiss", key=f"dismiss-{key}"):
                if _post("/skills/review/dismiss", {"raw_norm": key}):
                    st.rerun()

with verify_tab:
    try:
        matches = _get("/skills/review/embedding-matches", {"limit": 25})
    except httpx.HTTPError as exc:
        st.error(f"Failed to load embedding matches: {exc}")
        matches = []
    st.caption(f"{len(matches)} shown, least confident first")
    for match in matches:
        key = match["raw_norm"]
        with st.container(border=True):
            st.markdown(f"**{match['raw_example']}**")
            st.write(
                f"→ {match['skill_label'] or match['skill_id']} "
                f"(similarity {match['score']:.2f}, in {match['jd_job_count']} job(s))"
            )
            left, right = st.columns(2)
            if left.button("Confirm", key=f"confirm-{key}"):
                if _post(
                    "/skills/review/resolve",
                    {"raw_norm": key, "skill_id": match["skill_id"]},
                ):
                    st.rerun()
            if right.button("Reject", key=f"reject-{key}"):
                if _post("/skills/review/reject", {"raw_norm": key}):
                    st.rerun()
