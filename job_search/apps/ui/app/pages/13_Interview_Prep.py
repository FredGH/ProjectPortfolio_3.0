"""Interview Prep - drill a shared question bank with your own CV-grounded
answers, spaced repetition and the questions you are most likely to fumble
(PLAN.md Step 20)."""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings
from core.ui.theme import apply_theme

st.set_page_config(page_title="Interview Prep", layout="wide")
apply_theme()
st.title("Interview Prep")

with st.expander("User manual"):
    st.markdown(
        """
**Bank** shows the shared question bank per role family and generates what is
missing (Claude writes the questions and a second Claude pass checks the
answers; generating a topic costs a few dollars of tokens in total, so it asks
first, and finished topics are never generated again).

**Drill** shows what to practise now: questions that are due, then the ones
your CV covers least. Reveal the model answer, write your own answer (or ask
for a draft built only from your CV), then rate how well you recalled it from
0 (blank) to 5 (perfect): that schedules the next review. A draft sentence the
checker could not trace to your CV waits under *Needs your decision*: link it
to the CV item that backs it, or remove it.

**Gaps** lists the technical questions your CV evidences least. **Export**
gives an Anki file.
"""
    )

_base = get_settings().api_base_url
_MD_META = set("\\`*_{}[]()#+-.!|<>~$:&")
_DRAFT_COST = "about $0.03 to $0.08"


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
        method: `GET`, `POST` or `PUT`.
        path: The path, starting with `/`.
        **kwargs: Extra httpx arguments (`json`, `params`).

    Returns:
        The response.
    """
    timeout = 15.0 if method == "GET" else 600.0
    return httpx.request(method, f"{_base}{path}", timeout=timeout, **kwargs)  # type: ignore[arg-type]


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


try:
    bank_response = _call("GET", "/interview/bank")
    bank_response.raise_for_status()
    bank = bank_response.json()
except (httpx.HTTPError, ValueError) as exc:
    st.error(_plain(f"Failed to load the bank: {exc}"))
    st.stop()

categories = sorted(
    {r["qa_category"] for r in bank["rows"]} | {w["category"] for w in bank["work"]}
)
if not categories:
    st.info("No role families are configured yet.")
    st.stop()
category = st.selectbox(
    "Role family",
    categories,
    format_func=lambda c: c.replace("_", " "),
    key="iv-category",
)

_last = st.session_state.pop("iv-last-message", None)
if _last:
    st.success(_plain(_last))

drill_tab, gaps_tab, bank_tab, export_tab = st.tabs(["Drill", "Gaps", "Bank", "Export"])


def _show_error(response: httpx.Response) -> None:
    """Show an API error.

    Args:
        response: A non-2xx response.
    """
    st.error(_plain(_detail(response)))


with bank_tab:
    rows = [r for r in bank["rows"] if r["qa_category"] == category]
    if rows:
        st.dataframe(
            [
                {
                    "Topic": r["topic"],
                    "Approved": r["approved"],
                    "Needs review": r["needs_review"],
                }
                for r in rows
            ],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("Nothing generated for this role family yet.")
    work = [w for w in bank["work"] if w["category"] == category]
    if bank["unconfigured"] and category in bank["unconfigured"]:
        st.warning("No topics are configured for this role family.")
    if work:
        cost = sum(w["estimated_cost_usd"] for w in work)
        st.markdown(
            f"**{sum(w['missing'] for w in work)} question(s)** still to generate "
            f"across {len(work)} topic(s)."
        )
        if st.button("Generate what is missing", key="iv-generate"):
            st.session_state["iv-confirming"] = category
        if st.session_state.get("iv-confirming") == category:
            st.warning(
                f"This calls Claude many times and costs about ${cost:.2f}, and "
                "takes several minutes. Nothing is spent unless you confirm."
            )
            confirm_col, cancel_col = st.columns(2)
            if cancel_col.button("Cancel", key="iv-cancel"):
                st.session_state.pop("iv-confirming", None)
                st.rerun()
            if confirm_col.button("Confirm", key="iv-confirm"):
                st.session_state.pop("iv-confirming", None)
                with st.spinner("Generating - this can take several minutes"):
                    try:
                        answer = _call(
                            "POST",
                            "/interview/bank/generate",
                            json={"category": category},
                        )
                    except httpx.HTTPError as exc:
                        st.error(_plain(f"Generation failed: {exc}"))
                        st.stop()
                if answer.status_code != 200:
                    _show_error(answer)
                    st.stop()
                done = answer.json()
                errors = [r["error"] for r in done if r["error"]]
                st.session_state["iv-last-message"] = (
                    f"Generated {sum(r['approved'] for r in done)} question(s)."
                    + (f" Some topics stopped early: {errors[0]}" if errors else "")
                )
                st.rerun()
    elif rows:
        st.success("This role family's bank is complete.")


def _load_list(path: str) -> list[dict] | None:
    """Load a question list for the chosen role family.

    Args:
        path: `/interview/queue` or `/interview/gaps`.

    Returns:
        The questions, or None after showing an error.
    """
    try:
        response = _call("GET", path, params={"category": category})
    except httpx.HTTPError as exc:
        st.error(_plain(f"Failed to load: {exc}"))
        return None
    if response.status_code != 200:
        _show_error(response)
        return None
    return response.json()


def _evidence() -> list[dict]:
    """Load the CV items a sentence can be linked to.

    Returns:
        Evidence dicts with `id` and `label`; empty when unavailable.
    """
    try:
        response = _call("GET", "/interview/evidence")
    except httpx.HTTPError:
        return []
    return response.json() if response.status_code == 200 else []


def _show_pending(question: dict) -> None:
    """Show the draft sentences waiting for a decision.

    Args:
        question: The API's question model.
    """
    pending = [o for o in question["open_sentences"] if o["status"] == "pending"]
    if not pending:
        return
    st.subheader("Needs your decision")
    st.caption(
        "These sentences of your draft could not be traced to your CV. Link one "
        "to the CV item that backs it, or remove it."
    )
    evidence = _evidence()
    labels = {e["id"]: e["label"] for e in evidence}
    for item in pending:
        st.markdown(f"**{_plain(item['text'])}**")
        st.caption(_plain(item["issue"] or "no source"))
        choice = (
            st.selectbox(
                "Link to",
                options=list(labels),
                format_func=lambda i: labels[i],
                key=f"iv-link-select-{item['id']}",
            )
            if labels
            else None
        )
        link_col, remove_col = st.columns(2)
        for column, key, action, ref in (
            (link_col, f"iv-link-{item['id']}", "link", choice),
            (remove_col, f"iv-remove-{item['id']}", "remove", None),
        ):
            if column.button(
                action.capitalize(), key=key, disabled=action == "link" and not labels
            ):
                answer = _call(
                    "POST",
                    f"/interview/open-sentences/{item['id']}/decision",
                    json={"action": action, "evidence_ref": ref},
                )
                if answer.status_code != 200:
                    _show_error(answer)
                else:
                    st.rerun()


with drill_tab:
    queue = _load_list("/interview/queue")
    if queue is None:
        pass
    elif not queue:
        st.info("Nothing to drill here: no questions are due or generated yet.")
    else:
        question = queue[0]
        st.caption(
            f"{len(queue)} in the queue - {question['topic']} - difficulty "
            f"{question['difficulty']}/5"
        )
        st.markdown(f"### {_plain(question['question'])}")
        qid = question["id"]
        if st.checkbox("Show the model answer", key=f"iv-reveal-{qid}"):
            st.markdown(_plain(question["model_answer"]))
        answer_key = f"iv-answer-{qid}"
        if answer_key not in st.session_state:
            st.session_state[answer_key] = question["personal_answer"] or ""
        st.text_area("Your answer", key=answer_key, height=180)
        status = question["answer_status"]
        if status == "drafted":
            st.caption("Drafted from your CV - edit it, then save to make it yours.")
        elif status == "needs_review":
            st.warning("This draft has sentences waiting for your decision below.")
        save_col, draft_col = st.columns(2)
        if save_col.button("Save my answer", key="iv-save"):
            saved = _call(
                "PUT",
                f"/interview/questions/{qid}/answer",
                json={"answer": st.session_state[answer_key]},
            )
            if saved.status_code != 200:
                _show_error(saved)
            else:
                st.session_state["iv-last-message"] = "Your answer was saved."
                st.rerun()
        if draft_col.button("Draft from my CV", key="iv-draft"):
            st.session_state["iv-drafting"] = qid
        if st.session_state.get("iv-drafting") == qid:
            st.warning(
                f"Drafting calls Claude a few times and costs {_DRAFT_COST}. "
                "Nothing is spent unless you confirm."
                + (" This replaces your saved answer." if status == "written" else "")
            )
            go_col, no_col = st.columns(2)
            if no_col.button("Cancel", key="iv-draft-cancel"):
                st.session_state.pop("iv-drafting", None)
                st.rerun()
            if go_col.button("Confirm draft", key="iv-draft-confirm"):
                st.session_state.pop("iv-drafting", None)
                with st.spinner("Drafting from your CV"):
                    drafted = _call(
                        "POST",
                        f"/interview/questions/{qid}/draft",
                        json={"replace": status == "written"},
                    )
                if drafted.status_code != 200:
                    _show_error(drafted)
                else:
                    st.session_state.pop(answer_key, None)
                    st.rerun()
        _show_pending(question)
        st.markdown("**How well did you recall it?**")
        cols = st.columns(6)
        for quality, column in enumerate(cols):
            if column.button(str(quality), key=f"iv-rate-{quality}"):
                rated = _call(
                    "POST",
                    f"/interview/questions/{qid}/review",
                    json={"quality": quality},
                )
                if rated.status_code != 200:
                    _show_error(rated)
                else:
                    st.rerun()
        st.caption("0 = blank, 3 = recalled with effort, 5 = perfect.")

with gaps_tab:
    gaps = _load_list("/interview/gaps")
    if gaps is None:
        pass
    elif not gaps:
        st.info("No technical questions to score yet.")
    else:
        st.dataframe(
            [
                {
                    "Gap": round(g["cv_gap_score"] or 0.0, 2),
                    "Question": g["question"],
                    "Topic": g["topic"],
                    "Tags": ", ".join(g["tags"]),
                }
                for g in gaps
            ],
            hide_index=True,
            use_container_width=True,
        )
        st.caption(
            "Higher means your CV evidences these skills less, so you are more "
            "likely to fumble the question."
        )

with export_tab:
    try:
        exported = _call("GET", "/interview/export/anki", params={"category": category})
    except httpx.HTTPError as exc:
        st.error(_plain(f"Failed to prepare the export: {exc}"))
    else:
        if exported.status_code == 200 and exported.text.count("\n") > 3:
            st.download_button(
                "Download the Anki file",
                exported.text,
                file_name=f"interview_{category}.txt",
                mime="text/plain",
                key="iv-download",
            )
            st.caption("In Anki: File > Import, pick the file; it sets itself up.")
        else:
            st.info("Nothing to export yet.")
