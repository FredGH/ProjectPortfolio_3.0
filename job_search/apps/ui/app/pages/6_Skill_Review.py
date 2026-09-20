"""Skill review (PLAN.md Step 14) — resolve skill strings that did not map to
ESCO, and verify the ones the mapper matched on its own (by ESCO label or by
embedding similarity).

A resolution is remembered as an alias, so each string is fixed once and the
fix applies to every future CV and job description. Aliases are shared across
users (docs/tenancy.md: taxonomy is a shared zone).
"""

from __future__ import annotations

import re

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Skill Review", layout="wide")
st.title("Skill Review")
st.write(
    "Skills from job descriptions and your CV that could not be matched to the "
    "ESCO vocabulary, plus the matches the system made on its own (by ESCO label "
    "or by similarity) — check those, since a wrong match is otherwise invisible."
)

_API = get_settings().api_base_url

_ASCII_PUNCTUATION = re.compile(r"([!-/:-@\[-`{-~])")


def _plain(value: object) -> str:
    """Escape a value so Streamlit's markdown shows it literally.

    Skill strings come from third-party job descriptions, so a link, image or
    emphasis inside one must not become live markup. CommonMark lets any ASCII
    punctuation character be backslash-escaped.

    Args:
        value: The text to display.

    Returns:
        The text with every ASCII punctuation character backslash-escaped.
    """
    return _ASCII_PUNCTUATION.sub(r"\\\1", str(value))


def _score_suffix(score: float | None) -> str:
    """Format a similarity score for display, if there is one.

    Args:
        score: A cosine similarity, or None (a label match has none).

    Returns:
        " (0.62)" or the empty string.
    """
    return f" ({score:.2f})" if score is not None else ""


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


def _error_message(exc: httpx.HTTPStatusError) -> str:
    """Build a readable message from an HTTP error response.

    Uses the API's JSON ``detail`` when there is one (joining the ``msg`` of
    each item for a request-validation error), and falls back to the
    exception text for a non-JSON body such as a plain-text 500 or a proxy 502.

    Args:
        exc: The status error raised for the response.

    Returns:
        The message to show the user.
    """
    try:
        detail = exc.response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):
        detail = "; ".join(
            str(item.get("msg", item)) if isinstance(item, dict) else str(item)
            for item in detail
        )
    return str(detail) if detail else str(exc)


def _post(path: str, payload: dict) -> bool:
    """POST an action to the API, showing any error on the page.

    Args:
        path: The endpoint path.
        payload: The JSON body.

    Returns:
        True if the action succeeded; False if it failed, in which case the
        error (including a non-JSON error body) is shown instead of raised.
    """
    try:
        response = httpx.post(f"{_API}{path}", json=payload, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        st.error(_error_message(exc))
        return False
    except httpx.HTTPError as exc:
        st.error(f"Request failed: {exc}")
        return False
    return True


unmapped_tab, verify_tab = st.tabs(["Unmapped", "Auto-matches — verify"])

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
            st.markdown(f"**{_plain(item['raw_example'])}**")
            st.caption(
                f"In {item['jd_job_count']} job(s)"
                + (" · on your CV" if item["seen_in_cv"] else "")
            )
            if item["candidate_skill_id"]:
                suggestion = _plain(
                    item["candidate_label"] or item["candidate_skill_id"]
                )
                score = _score_suffix(item["candidate_score"])
                # On a rejected row the candidate IS the match the reviewer
                # just threw out, so offering "Accept suggestion" would
                # undo their decision in one click. Show what was rejected
                # instead — it is still the useful context for choosing.
                if item["review_status"] == "rejected":
                    st.caption(f"Previously rejected: {suggestion}{score}")
                elif st.button(
                    f"Accept suggestion: {suggestion}{score}",
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
        matches = _get("/skills/review/auto-matches", {"limit": 25})
    except httpx.HTTPError as exc:
        st.error(f"Failed to load auto-matches: {exc}")
        matches = []
    st.caption(
        f"{len(matches)} shown — label matches whose skill has a different name "
        "first, then similarity matches (least confident first), then the rest"
    )
    for match in matches:
        key = match["raw_norm"]
        with st.container(border=True):
            st.markdown(f"**{_plain(match['raw_example'])}**")
            how = (
                "exact ESCO label match"
                if match["method"] == "label"
                else f"similarity {match['score']:.2f}"
            )
            st.write(
                f"→ {_plain(match['skill_label'] or match['skill_id'])} — {how}, "
                f"in {match['jd_job_count']} job(s)"
                + (" · on your CV" if match["seen_in_cv"] else "")
            )
            if match["suspicious"]:
                st.warning(
                    "Matched through an alternative ESCO label, not the skill's "
                    "own name — check it really is the same thing."
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
