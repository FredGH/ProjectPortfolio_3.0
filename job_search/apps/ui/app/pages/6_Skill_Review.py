"""Skill review (PLAN.md Step 14) — resolve skill strings that did not map to
ESCO, and verify the ones the mapper matched on its own (by ESCO label or by
embedding similarity).

A resolution is remembered as an alias, so each string is fixed once and the
fix applies to every future CV and job description. Aliases are shared across
users (docs/tenancy.md: taxonomy is a shared zone). A decision can be withdrawn
from the Decisions tab (reopen).
"""

from __future__ import annotations

import re

import httpx
import streamlit as st

from core.settings import get_settings

_USER_GUIDE = """
#### What this page is for

Every skill written in a job description or on your CV is reduced to a
normalised string and looked up in this order: **(1)** a curated *alias*,
**(2)** an exact *ESCO label*, **(3)** the nearest ESCO skill by *similarity*
(accepted at 0.85 or above), **(4)** otherwise it is left *unmapped* and waits
for you here.

Whatever you decide here is stored as an alias. It is **shared by every user**
and applies to **every future CV and job description**, so a string is fixed
once. An alias you create outranks ESCO and the seed file
(`config/skill_aliases.yml`), and a re-sync of that file never overwrites it.

#### Tab 1 — Unmapped

Strings the mapper could not place. Each card shows the string, how many jobs
mention it, whether it is on your CV, and the nearest ESCO skill as a suggestion
when there is one.

- **Accept suggestion: …** maps the string to the suggested ESCO skill.
  *Consequence:* the string becomes a permanent alias and is marked *resolved*.
  Any future string that reduces to it — including a `Name (qualifier)` form such
  as `MySQL (RDS)` whose head is that string — maps to the same skill. It is
  not offered on a *Previously rejected* card, because that suggestion is the
  one that was already thrown out.
- **Search ESCO / custom skills**, then **Map to selected** does the same for
  any skill you pick. The search is a literal substring match on every label of
  a skill (preferred, alternative and hidden), best match first.
- **Mark as custom skill** creates a `custom:` skill with the label you type (or
  reuses one with the same label) and maps the string to it. Use it for tools
  ESCO lacks. *Consequence:* custom skills are never deleted automatically, so
  point every spelling of one tool at the same label — two labels mean two
  separate skills in a gap analysis.
- **Dismiss** means "this is not a skill". *Consequence:* the string is never
  queued again and stays unmapped, so it counts as neither a match nor a gap.
  Only an unmapped string can be dismissed. It can be brought back from the
  Decisions tab.

A card marked **Previously rejected** came from *Reject* (tab 2) or from
reopening a resolved decision (tab 3). The skill named is the one that was
turned down, shown only as context.

#### Tab 2 — Auto-matches — verify

Matches the system made on its own, by exact ESCO label or by similarity. A wrong
match is otherwise invisible, so check them here.

- A **warning** marks a label match where the string is not the skill's own name
  — it matched through an alternative or hidden ESCO label of a differently named
  skill (for example a programming language filed under "computer programming").
  These are listed first, then similarity matches (least confident first), then
  the remaining label matches.
- **Confirm** agrees with the match. *Consequence:* it is saved as an alias and
  marked *resolved*. From then on it is your decision, not an automatic one, so
  a re-map never changes it.
- **Reject** says the match is wrong. *Consequence:* the mapping is removed and
  the card moves to the Unmapped tab as *Previously rejected*. It is protected
  from automatic re-mapping, so it cannot silently match the same wrong skill
  again — it stays unmapped until you resolve or dismiss it.
- Leaving a match unreviewed is fine: it stays in force as it is.

#### Tab 3 — Decisions — reopen

Every string you resolved or dismissed, with the skill it was resolved to. Search
matches the string or the skill's label.

- **Reopen** withdraws the decision. *For a resolved string:* the alias is
  deleted, so the old target stops applying to future strings, and the card
  returns to the Unmapped tab as *Previously rejected* with the old target as
  context. Then resolve it to the right skill, or dismiss it. *For a dismissed
  string:* it returns to the Unmapped tab as an ordinary open string.
- A custom skill created by an earlier decision is kept, because other strings
  may point at it.
- Not reopenable here: a string mapped by a curated seed alias (edit
  `config/skill_aliases.yml` instead), an automatic match (use *Reject*), and a
  string that is already unmapped.

#### After you change something

A decision takes effect immediately for strings mapped from now on. Rows that
were already mapped, and the data built from them, are **not** rewritten by this
page:

1. **Job descriptions:** run `map-skills --remap-all-auto` to re-map every
   automatic mapping (your decisions are never touched), then
   `dbt run --select silver__skill silver__bridge_job_skill` to refresh the
   job–skill bridge.
2. **Your CV:** `map-cv-skills` fills only skills that have no id yet. A skill
   that already carries an id keeps it, even after you reopen or change its
   decision.
3. After changing the similarity threshold, `map-skills --remap-unresolved`
   re-runs only the unmapped and similarity-matched strings.

#### Good to know

- Resolving, dismissing, confirming, rejecting and reopening all change the
  shared vocabulary for every user.
- There is no bulk undo. To reverse a decision, reopen it.
- Skill strings come from third-party job descriptions, so they are shown as
  plain text.
"""

st.set_page_config(page_title="Skill Review", layout="wide")
st.title("Skill Review")
with st.expander("User Guide", expanded=False):
    st.markdown(_USER_GUIDE)
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


unmapped_tab, verify_tab, decisions_tab = st.tabs(
    ["Unmapped", "Auto-matches — verify", "Decisions — reopen"]
)

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

with decisions_tab:
    decision_query = st.text_input(
        "Search decisions (a string or its skill)", key="decision-q"
    )
    decision_params: dict[str, object] = {"limit": 25}
    if decision_query:
        decision_params["q"] = decision_query
    try:
        decisions = _get("/skills/review/decisions", decision_params)
    except httpx.HTTPError as exc:
        st.error(f"Failed to load decisions: {exc}")
        decisions = []
    st.caption(f"{len(decisions)} shown, most-used first")
    for decision in decisions:
        key = decision["raw_norm"]
        with st.container(border=True):
            st.markdown(f"**{_plain(decision['raw_example'])}**")
            usage = f"in {decision['jd_job_count']} job(s)" + (
                " · on your CV" if decision["seen_in_cv"] else ""
            )
            if decision["review_status"] == "dismissed":
                st.write(f"Dismissed — not treated as a skill, {usage}")
            else:
                target = _plain(decision["skill_label"] or decision["skill_id"])
                st.write(f"→ {target} — resolved, {usage}")
            if st.button("Reopen", key=f"reopen-{key}"):
                if _post("/skills/review/reopen", {"raw_norm": key}):
                    st.rerun()
