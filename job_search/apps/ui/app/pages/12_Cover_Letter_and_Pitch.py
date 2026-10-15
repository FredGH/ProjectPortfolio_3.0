"""Cover Letter & Pitch - a checked cover letter and a 30/60/90 second pitch
for one job (PLAN.md Step 19). Every factual sentence shows what it rests on;
a sentence that could not be traced waits here for your decision."""

from __future__ import annotations

import httpx
import streamlit as st

from core.artefacts.render import (
    RenderError,
    letter_download_bundle,
    pitch_download_bundle,
)
from core.artefacts.schema import CoverLetter, Pitch, word_count
from core.settings import get_settings
from core.ui.theme import apply_theme

st.set_page_config(page_title="Cover Letter & Pitch", layout="wide")
apply_theme()
st.title("Cover Letter & Pitch")

with st.expander("User manual"):
    st.markdown(
        """
Pick a job, then write its **cover letter** and **pitch**. Both are built only
from your CV, the job posting and the company research saved on the Company
Intel page - nothing is researched here. Every factual sentence shows the
sources it rests on. The job title in the letter and pitch is written by the
app, so it is always exact.

Writing calls Claude several times and costs about 10 cents per document, so
it asks you to confirm first. A sentence that could not be traced to a source
is listed under *Needs your decision*: link it to a source that really backs
it, or remove it. A document is approved - and can be downloaded - only when
none is left.
"""
    )

_base = get_settings().api_base_url
_MD_META = set("\\`*_{}[]()#+-.!|<>~$:&")
_COST = "about $0.10"
_KINDS = {
    "cover_letter": ("Cover letter", "cover-letter", "art-generate-letter", "letter"),
    "pitch": ("Pitch", "pitch", "art-generate-pitch", "pitch"),
}


def _plain(text: object) -> str:
    """Escape text so Streamlit renders it literally, never as markdown.

    Args:
        text: Untrusted text (anything `str()`-able).

    Returns:
        The text with every markdown metacharacter backslash-escaped.
    """
    return "".join(f"\\{ch}" if ch in _MD_META else ch for ch in str(text))


def _get(path: str) -> httpx.Response:
    """GET from the API.

    Args:
        path: The path, starting with `/`.

    Returns:
        The response.
    """
    return httpx.get(f"{_base}{path}", timeout=15.0)


def _post(path: str, json: dict) -> httpx.Response:
    """POST to the API (writing can take a minute or two).

    Args:
        path: The path, starting with `/`.
        json: The JSON body.

    Returns:
        The response.
    """
    return httpx.post(f"{_base}{path}", json=json, timeout=600.0)


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
if _linked_job in labels and "art_linked_job" not in st.session_state:
    st.session_state["art_linked_job"] = _linked_job
    st.session_state["art-job"] = _linked_job
selected = st.selectbox(
    "Job",
    options=list(labels),
    format_func=lambda job_id: labels[job_id],
    key="art-job",
)

try:
    loaded = _get(f"/artefacts/jobs/{selected}")
except httpx.HTTPError as exc:
    st.error(_plain(f"Failed to load the documents: {exc}"))
    st.stop()
if loaded.status_code == 409:
    st.info(_plain(_detail(loaded)))
    st.stop()
if loaded.status_code != 200:
    st.error(_plain(f"Failed to load the documents: {_detail(loaded)}"))
    st.stop()
view = loaded.json()
evidence = {e["id"]: e for e in view["evidence"]}

_last = st.session_state.pop("art-last-message", None)
if _last:
    st.success(_plain(_last))

if not view["has_saved_research"]:
    st.info(
        "The letter will rely on the job posting alone. Research the company on "
        "the Company Intel page for a richer one - nothing is researched "
        "automatically."
    )


def _source_labels(ids: list[str]) -> str:
    """Name the evidence a sentence cites.

    Args:
        ids: The sentence's source ids.

    Returns:
        The labels of the ids the evidence list knows, joined by `;`.
    """
    return "; ".join(
        _plain(evidence[i]["label"]) if i in evidence else _plain(i) for i in ids
    )


def _show_sentences(sentences: list[dict]) -> None:
    """Show sentences, each followed by what it rests on.

    Args:
        sentences: Sentence dicts from the stored document.
    """
    for sentence in sentences:
        st.markdown(_plain(sentence["text"]))
        if sentence["kind"] == "composed":
            st.caption("written by the app from the job title")
        elif sentence["kind"] == "claim":
            note = f"sources: {_source_labels(sentence['sources'])}"
            if sentence["flag"] in ("orphan", "unsupported"):
                note += " - awaiting your decision"
            elif sentence["flag"] == "linked":
                note += " - linked by you"
            st.caption(note)


def _show_document(kind: str, content: dict) -> None:
    """Show a letter or a pitch.

    Args:
        kind: `cover_letter` or `pitch`.
        content: The stored document.
    """
    if kind == "cover_letter":
        letter = CoverLetter.model_validate(content)
        for line in (letter.sender, letter.contact, letter.date, letter.recipient):
            if line:
                st.markdown(_plain(line))
        st.markdown(f"**{_plain(letter.reference)}**")
        for paragraph in letter.paragraphs:
            _show_sentences([s.model_dump() for s in paragraph.sentences])
        st.markdown(_plain(letter.sign_off).replace("\n", "  \n"))
        return
    pitch = Pitch.model_validate(content)
    tabs = st.tabs([f"{v.seconds} seconds" for v in pitch.variants])
    for tab, variant in zip(tabs, pitch.variants, strict=True):
        with tab:
            _show_sentences([s.model_dump() for s in variant.sentences])
            words = word_count(" ".join(s.text for s in variant.sentences))
            st.caption(f"{words} words")


def _decide(kind: str, open_id: str, action: str, evidence_ref: str | None) -> None:
    """Send a link / remove decision and refresh the page.

    Args:
        kind: `cover_letter` or `pitch`.
        open_id: The open sentence's id.
        action: `link` or `remove`.
        evidence_ref: For a link, the chosen evidence id.
    """
    try:
        answer = _post(
            f"/artefacts/open-sentences/{open_id}/decision",
            {
                "kind": kind,
                "job_group_id": selected,
                "action": action,
                "evidence_ref": evidence_ref,
            },
        )
    except httpx.HTTPError as exc:
        st.error(_plain(f"Could not save the decision: {exc}"))
        return
    if answer.status_code != 200:
        st.error(_plain(_detail(answer)))
        return
    st.rerun()


def _show_pending(kind: str, artefact: dict) -> None:
    """Show the sentences waiting for a decision.

    Args:
        kind: `cover_letter` or `pitch`.
        artefact: The API's artefact model.
    """
    pending = [o for o in artefact["open_sentences"] if o["status"] == "pending"]
    if not pending:
        return
    st.subheader("Needs your decision")
    st.caption(
        "These sentences could not be traced to a source. Link one to a source "
        "that really backs it, or remove it."
    )
    for item in pending:
        st.markdown(f"**{_plain(item['text'])}**")
        st.caption(_plain(item["issue"] or "no source"))
        choice = st.selectbox(
            "Link to",
            options=list(evidence),
            format_func=lambda i: evidence[i]["label"],
            key=f"art-link-select-{item['id']}",
        )
        link_col, remove_col = st.columns(2)
        if link_col.button("Link", key=f"art-link-{item['id']}"):
            _decide(kind, item["id"], "link", choice)
        if remove_col.button("Remove", key=f"art-remove-{item['id']}"):
            _decide(kind, item["id"], "remove", None)


def _show_downloads(kind: str, content: dict) -> None:
    """Offer the approved document's files.

    Args:
        kind: `cover_letter` or `pitch`.
        content: The stored document.
    """
    try:
        bundle = (
            letter_download_bundle(CoverLetter.model_validate(content))
            if kind == "cover_letter"
            else pitch_download_bundle(Pitch.model_validate(content))
        )
    except RenderError as exc:
        st.error(_plain(f"The files could not be prepared: {exc}"))
        return
    for name, mime, data in bundle:
        st.download_button(
            f"Download {name}",
            data,
            file_name=name,
            mime=mime,
            key=f"art-download-{name}",
        )


def _section(kind: str) -> None:
    """Show one document's generate button, state and content.

    Args:
        kind: `cover_letter` or `pitch`.
    """
    title, path, button_key, noun = _KINDS[kind]
    artefact = view[kind]
    st.header(title)
    label = f"Write the {noun} again" if artefact else f"Write the {noun}"
    if st.button(label, key=button_key):
        st.session_state["art-confirming"] = (kind, selected)
    if st.session_state.get("art-confirming") == (kind, selected):
        st.warning(
            f"Writing the {noun} calls Claude several times and costs {_COST}. "
            "Nothing is spent unless you confirm."
        )
        confirm_col, cancel_col = st.columns(2)
        if cancel_col.button("Cancel", key="art-cancel"):
            st.session_state.pop("art-confirming", None)
            st.rerun()
        if confirm_col.button("Confirm", key="art-confirm"):
            st.session_state.pop("art-confirming", None)
            with st.spinner(f"Writing the {noun} - this can take a minute or two"):
                try:
                    answer = _post(f"/artefacts/jobs/{selected}/{path}", {})
                except httpx.HTTPError as exc:
                    st.error(_plain(f"Could not write it: {exc}"))
                    st.stop()
            if answer.status_code != 200:
                st.error(_plain(_detail(answer)))
                st.stop()
            st.session_state["art-last-message"] = f"The {noun} was written."
            st.rerun()
    if artefact is None:
        st.caption(f"No {noun} yet.")
        return
    status = artefact["status"]
    if status == "failed":
        st.error(_plain(f"This attempt failed: {artefact['error_message']}"))
        return
    pending = [o for o in artefact["open_sentences"] if o["status"] == "pending"]
    if status == "needs_review":
        st.warning(f"{len(pending)} sentence(s) await your decision.")
    else:
        st.success("Approved: every sentence is traced and checked.")
    if artefact["content"] is not None:
        _show_document(kind, artefact["content"])
    _show_pending(kind, artefact)
    if status == "approved" and artefact["content"] is not None:
        _show_downloads(kind, artefact["content"])


_section("cover_letter")
_section("pitch")
