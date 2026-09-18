"""CV correction pass (PLAN.md Step 13) — upload a CV to extract a
truth base, or edit and re-save an existing one. Uses `st.data_editor`
for every list-shaped section (skills, experience bullets, education,
qualifications, publications) rather than one widget per nested field,
so adding/removing a row is a native grid action instead of bespoke
per-field UI.
"""

from __future__ import annotations

import time

import httpx
import pandas as pd
import streamlit as st

from core.cv.bullet_id import compute_bullet_id
from core.settings import get_settings

st.set_page_config(page_title="CV Editor", layout="wide")
st.title("CV Editor")

_settings = get_settings()

_USER_GUIDE_MARKDOWN = """
**What this page does:** upload a CV (PDF) to extract it into
structured fields — identity, skills, experience, education, and so
on. Review and correct any field, then **Save** — every save creates
a new version you can name and come back to later.

**Sections:**
- **Identity, Headline, Email, Phone, LinkedIn URL, Nationality** —
  the header block under your name; edit directly.
- **Summary** — the professional summary/profile paragraph, if the CV
  has one.
- **Skills** — one row per skill; add or remove rows freely.
- **Experience** — one accordion per role.
  - **+ Add experience** adds a blank entry for a role extraction
    missed — fill it in by hand.
  - **↑ Move up / ↓ Move down** on each entry reorders it.
- **Education** — institution, grade, qualification, start/end year.
- **Publications** — citation, authors (comma-separated), conference,
  year.
- **Professional Qualifications & Continuous Personal Development** —
  certifications and courses, in one merged list.
- **Projects** — free-form rows.
- **Activities & Interests** — name, organisation, start/end per entry
  (e.g. "Mentor" / "MyJobGlasses" / "2020" / blank for ongoing).
- **Re-extract from a new CV** — upload a different or updated CV
  file to run extraction again from scratch.
- **Version history** — every save is kept, each showing how long its
  extraction took end-to-end (blank for a version from a manual Save,
  since no extraction ran). **Restore** copies an older version's
  content forward as a new current version — nothing is ever
  overwritten in place.

**If a section comes back empty, blank, or wrong:** just edit it —
every field here is a normal editable box or table, whether or not
extraction filled it in. For a missing role, use **+ Add experience**
rather than waiting on a re-extract to catch it.

**Why this happens, and how to reduce it:** CV extraction always runs
on a local, free model (Ollama's llama3.1:8b), by design — it keeps
every extraction private and free of API cost, but it's noticeably
weaker than a larger hosted model at faithfully parsing a long, dense
CV, and can drop or garble a section rather than just mis-format it.
If manual correction is costing you real time on every extraction,
the fix isn't another prompt tweak — it's routing CV extraction to a
stronger model instead. That's a deliberate, recorded architecture
choice (see `DECISIONS.md`), so it's worth raising explicitly as its
own decision rather than working around indefinitely.
"""


def _fetch_truth_base() -> dict | None:
    """Fetch the current CV truth base, if one has been extracted yet.

    Returns:
        The parsed `GET /cv/truth-base` response body.

    Raises:
        httpx.HTTPError: If the request fails.
    """
    response = httpx.get(f"{_settings.api_base_url}/cv/truth-base", timeout=10.0)
    response.raise_for_status()
    return response.json()


def _fetch_version_history() -> list[dict]:
    """Fetch every version of the CV, newest first.

    Returns:
        The parsed `GET /cv/truth-base/versions` response body.

    Raises:
        httpx.HTTPError: If the request fails.
    """
    response = httpx.get(
        f"{_settings.api_base_url}/cv/truth-base/versions", timeout=10.0
    )
    response.raise_for_status()
    return response.json()


_STEP_LABELS: dict[str, str] = {
    "parsing_document": "Parsing document",
    "extracting_fields": "Extracting fields",
    "saving": "Saving",
}

_STEP_ICONS: dict[str, str] = {
    "pending": "⬜",
    "running": "⏳",
    "done": "✅",
    "failed": "❌",
}


def _poll_extraction_job(job_id: str) -> dict:
    """Fetch one extraction job's current status.

    Args:
        job_id: The job id returned by `POST /cv/extract`.

    Returns:
        The parsed `GET /cv/extract/jobs/{job_id}` response body.

    Raises:
        httpx.HTTPError: If the request fails, including a 404 for an
            unknown/expired job id.
    """
    response = httpx.get(
        f"{_settings.api_base_url}/cv/extract/jobs/{job_id}", timeout=10.0
    )
    response.raise_for_status()
    return response.json()


def _render_job_progress(job: dict) -> None:
    """Render an extraction job's steps as a live checklist.

    Args:
        job: A `GET /cv/extract/jobs/{job_id}` response body.
    """
    state = {
        "queued": "running",
        "running": "running",
        "succeeded": "complete",
        "failed": "error",
    }[job["status"]]
    with st.status(f"Extracting CV — {job['status']}", state=state, expanded=True):
        for step in job["steps"]:
            label = _STEP_LABELS.get(step["name"], step["name"])
            icon = _STEP_ICONS[step["status"]]
            if step["duration_seconds"] is not None:
                st.write(f"{icon} {label} ({step['duration_seconds']:.1f}s)")
            else:
                st.write(f"{icon} {label}")


def _clean_editor_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Clean up `st.data_editor` output before building the save payload.

    `num_rows="dynamic"` always leaves one unfilled row at the bottom
    for adding new entries, and needs a starter row to infer column
    dtypes when a section starts out empty — both come back as
    all-NaN records, dropped here. Every remaining NaN cell (a single
    field left blank on an otherwise-filled row) is normalized to
    `None`, so a numeric column like "year" round-trips as JSON `null`
    instead of the non-standard `NaN`, and downstream field access
    never has to special-case pandas' NaN.

    Args:
        df: The edited DataFrame, as returned by `st.data_editor`.

    Returns:
        `df` with all-blank rows removed and every NaN replaced by
        `None`.
    """
    df = df[~df.isna().all(axis=1)].reset_index(drop=True)
    return df.where(pd.notnull(df), None)


def _upload_and_start_extraction() -> None:
    """Render a CV uploader and, on submit, start an extraction job.

    Stores the returned job id in session state and reruns so the
    top-level polling block picks it up immediately.
    """
    uploaded = st.file_uploader("Upload CV (PDF)", type=["pdf"], key="cv_uploader")
    if uploaded is not None and st.button("Extract", key="extract_button"):
        try:
            response = httpx.post(
                f"{_settings.api_base_url}/cv/extract",
                files={"file": (uploaded.name, uploaded.getvalue())},
                timeout=10.0,
            )
            response.raise_for_status()
            st.session_state["cv_extraction_job_id"] = response.json()["job_id"]
            st.rerun()
        except httpx.HTTPError as exc:
            st.error(f"Failed to start extraction: {exc}")


with st.expander("User Guide"):
    st.markdown(_USER_GUIDE_MARKDOWN)

try:
    current = _fetch_truth_base()
except httpx.HTTPError as exc:
    st.error(f"Failed to load CV truth base: {exc}")
    current = None

job_id = st.session_state.get("cv_extraction_job_id")
if job_id is not None:
    try:
        job = _poll_extraction_job(job_id)
    except httpx.HTTPError as exc:
        st.error(f"Lost track of the extraction job — please retry: {exc}")
        del st.session_state["cv_extraction_job_id"]
    else:
        _render_job_progress(job)
        if job["status"] in {"queued", "running"}:
            time.sleep(1)
            st.rerun()
        elif job["status"] == "succeeded":
            del st.session_state["cv_extraction_job_id"]
            st.success(f"Extracted as version {job['version']}.")
            st.rerun()
        else:
            del st.session_state["cv_extraction_job_id"]
            failed_step = _STEP_LABELS.get(job["failed_step"], job["failed_step"])
            st.error(f"Extraction failed at {failed_step}: {job['error']}")
elif current is None:
    st.info("No CV on file yet — upload one to extract a truth base.")
    _upload_and_start_extraction()
else:
    truth_base = current["truth_base"]
    version_caption = f"Version {current['version']}"
    if current["label"]:
        version_caption += f' — "{current["label"]}"'
    if current["extraction_seconds"] is not None:
        version_caption += f" — extracted in {current['extraction_seconds']:.1f}s"
    st.caption(version_caption)

    with st.expander("Re-extract from a new CV"):
        _upload_and_start_extraction()

    with st.expander("Version history"):
        try:
            history = _fetch_version_history()
        except httpx.HTTPError as exc:
            st.error(f"Failed to load version history: {exc}")
            history = []
        if history:
            header_cols = st.columns([1, 3, 2, 3, 2])
            for col, label in zip(
                header_cols,
                ["Version", "Name", "Extraction time", "Saved at", ""],
                strict=True,
            ):
                col.markdown(f"**{label}**")
        for entry in history:
            cols = st.columns([1, 3, 2, 3, 2])
            cols[0].write(f"v{entry['version']}")
            cols[1].write(entry["label"] or "—")
            extraction_seconds = entry["extraction_seconds"]
            cols[2].write(
                f"{extraction_seconds:.1f}s" if extraction_seconds is not None else "—"
            )
            cols[3].write(entry["created_at"])
            if entry["version"] == current["version"]:
                cols[4].write("current")
            elif cols[4].button("Restore", key=f"restore_{entry['version']}"):
                try:
                    response = httpx.post(
                        f"{_settings.api_base_url}/cv/truth-base/versions/"
                        f"{entry['version']}/restore",
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    st.success(f"Restored as version {response.json()['version']}.")
                    st.rerun()
                except httpx.HTTPError as exc:
                    st.error(f"Restore failed: {exc}")

    identity = st.text_input("Identity", value=truth_base["identity"])
    headline = st.text_input("Headline", value=truth_base["headline"])
    email = st.text_input("Email", value=truth_base["email"] or "")
    phone = st.text_input("Phone", value=truth_base["phone"] or "")
    linkedin_url = st.text_input("LinkedIn URL", value=truth_base["linkedin_url"] or "")
    nationality = st.text_input("Nationality", value=truth_base["nationality"] or "")
    summary = st.text_area("Summary", value=truth_base["summary"] or "", height=120)

    st.subheader("Skills")
    skills_df = pd.DataFrame(
        [
            {
                "name": s["name"],
                "years": s["years"],
                "last_used": s["last_used"],
            }
            for s in truth_base["skills"]
        ],
        columns=["name", "years", "last_used"],
    )
    edited_skills = st.data_editor(skills_df, num_rows="dynamic", key="skills_editor")

    st.subheader("Experience")

    # Experience entries are edited by stable id (not list position), so
    # reordering never disturbs which widget key holds which entry's
    # data. Re-seeded whenever a different version loads (a fresh
    # extraction, a save, or a restore) so stale local edits/additions
    # from a prior version never leak into the newly loaded one.
    if st.session_state.get("experience_version") != current["version"]:
        st.session_state["experience_ids"] = list(range(len(truth_base["experience"])))
        st.session_state["new_experience_entries"] = {}
        # Whether each entry starts expanded — set once, here or when an
        # entry is added below, and never recomputed afterward. Streamlit
        # 1.39's `st.expander` has no `key`, so it can't remember a
        # user's own expand/collapse click the way a keyed widget can;
        # recomputing this from the live company/title values would
        # force the expander shut the instant those fields go from
        # blank to filled, fighting anyone typing into a new entry.
        st.session_state["experience_expanded"] = {
            i: False for i in range(len(truth_base["experience"]))
        }
        st.session_state["experience_version"] = current["version"]

    experience_ids = st.session_state["experience_ids"]
    new_experience_entries = st.session_state["new_experience_entries"]
    experience_expanded = st.session_state["experience_expanded"]

    experience_rows = []
    for position, exp_id in enumerate(experience_ids):
        if exp_id < len(truth_base["experience"]):
            exp = truth_base["experience"][exp_id]
        else:
            exp = new_experience_entries.setdefault(
                exp_id,
                {
                    "company": "",
                    "title": "",
                    "start": "",
                    "end": None,
                    "bullets": [],
                    "tech": [],
                    "metrics": [],
                },
            )
        company_for_header = st.session_state.get(f"company_{exp_id}", exp["company"])
        title_for_header = st.session_state.get(f"title_{exp_id}", exp["title"])
        header = (
            f"{company_for_header} — {title_for_header}"
            if company_for_header or title_for_header
            else "New experience entry"
        )
        with st.expander(header, expanded=experience_expanded.setdefault(exp_id, True)):
            move_up, move_down, _spacer = st.columns([1, 1, 6])
            if move_up.button(
                "↑ Move up", key=f"move_up_{exp_id}", disabled=position == 0
            ):
                experience_ids[position - 1], experience_ids[position] = (
                    experience_ids[position],
                    experience_ids[position - 1],
                )
                st.rerun()
            if move_down.button(
                "↓ Move down",
                key=f"move_down_{exp_id}",
                disabled=position == len(experience_ids) - 1,
            ):
                experience_ids[position + 1], experience_ids[position] = (
                    experience_ids[position],
                    experience_ids[position + 1],
                )
                st.rerun()

            company = st.text_input(
                "Company", value=exp["company"], key=f"company_{exp_id}"
            )
            title = st.text_input("Title", value=exp["title"], key=f"title_{exp_id}")
            start = st.text_input(
                "Start", value=exp["start"] or "", key=f"start_{exp_id}"
            )
            end = st.text_input("End", value=exp["end"] or "", key=f"end_{exp_id}")
            bullets_df = pd.DataFrame(
                [{"text": b["text"]} for b in exp["bullets"]], columns=["text"]
            )
            edited_bullets = st.data_editor(
                bullets_df, num_rows="dynamic", key=f"bullets_{exp_id}"
            )
            experience_rows.append(
                {
                    "company": company,
                    "title": title,
                    "start": start or None,
                    "end": end or None,
                    "bullets": _clean_editor_rows(edited_bullets)["text"].tolist(),
                    "tech": exp["tech"],
                    "metrics": exp["metrics"],
                }
            )

    if st.button("+ Add experience"):
        next_id = max(experience_ids, default=-1) + 1
        new_experience_entries[next_id] = {
            "company": "",
            "title": "",
            "start": "",
            "end": None,
            "bullets": [],
            "tech": [],
            "metrics": [],
        }
        experience_ids.append(next_id)
        st.rerun()

    st.subheader("Education")
    education_df = pd.DataFrame(
        [
            {
                "institution": e["institution"],
                "grade": e["grade"],
                "qualification": e["qualification"],
                "start": e["start"],
                "end": e["end"],
            }
            for e in truth_base["education"]
        ],
        columns=["institution", "grade", "qualification", "start", "end"],
    )
    edited_education = st.data_editor(
        education_df, num_rows="dynamic", key="education_editor"
    )

    st.subheader("Publications")
    publications_df = pd.DataFrame(
        [
            {
                "citation": p["citation"],
                "authors": ", ".join(p["authors"]),
                "conference": p["conference"],
                "year": p["year"],
            }
            for p in truth_base["publications"]
        ],
        columns=["citation", "authors", "conference", "year"],
    )
    edited_publications = st.data_editor(
        publications_df,
        num_rows="dynamic",
        key="publications_editor",
        column_config={"year": st.column_config.NumberColumn("year", format="%d")},
    )

    st.subheader("Professional Qualifications & Continuous Personal Development")
    qualifications_df = pd.DataFrame(
        [{"name": q["name"], "year": q["year"]} for q in truth_base["qualifications"]],
        columns=["name", "year"],
    )
    edited_qualifications = st.data_editor(
        qualifications_df,
        num_rows="dynamic",
        key="qualifications_editor",
        column_config={"year": st.column_config.NumberColumn("year", format="%d")},
    )

    st.subheader("Projects")
    projects_df = pd.DataFrame(
        [
            {
                "name": p["name"],
                "description": p["description"],
                "tech": ", ".join(p["tech"]),
                "url": p["url"] or "",
            }
            for p in truth_base["projects"]
        ],
        columns=["name", "description", "tech", "url"],
    )
    edited_projects = st.data_editor(
        projects_df, num_rows="dynamic", key="projects_editor"
    )

    st.subheader("Activities & Interests")
    activities_df = pd.DataFrame(
        [
            {
                "name": a["name"],
                "organisation": a["organisation"],
                "start": a["start"],
                "end": a["end"],
            }
            for a in truth_base["activities"]
        ],
        columns=["name", "organisation", "start", "end"],
    )
    edited_activities = st.data_editor(
        activities_df, num_rows="dynamic", key="activities_editor"
    )

    version_label = st.text_input(
        "Version name",
        value="",
        help='Required — shown in Version history, e.g. "Before I added the AI '
        'section".',
    )
    if st.button("Save", disabled=not version_label.strip()):
        new_truth_base = {
            "identity": identity,
            "headline": headline,
            "email": email or None,
            "phone": phone or None,
            "linkedin_url": linkedin_url or None,
            "nationality": nationality or None,
            "summary": summary or None,
            "locations": truth_base["locations"],
            "work_auth": truth_base["work_auth"],
            "skills": [
                {
                    "name": row["name"],
                    "canonical_id": None,
                    "years": row["years"],
                    "last_used": row["last_used"],
                    "evidence_refs": [],
                }
                for row in _clean_editor_rows(edited_skills).to_dict("records")
            ],
            "experience": [
                {
                    **row,
                    # Recomputed, not carried over from the pre-edit
                    # bullets: compute_bullet_id is deterministic, so an
                    # unedited bullet gets back the exact ID it already
                    # had, and an edited or reordered one correctly gets
                    # a new one (see core.cv.bullet_id's docstring).
                    # The first argument is the *experience entry's*
                    # index (matching extract.py's own call), not the
                    # bullet's position within it — every bullet in one
                    # experience entry shares the same experience_index
                    # and is distinguished from its siblings by text.
                    "bullets": [
                        {"bullet_id": compute_bullet_id(exp_index, text), "text": text}
                        for text in row["bullets"]
                    ],
                }
                for exp_index, row in enumerate(experience_rows)
            ],
            "education": [
                {
                    "institution": row["institution"],
                    "grade": row["grade"],
                    "qualification": row["qualification"],
                    "start": row["start"],
                    "end": row["end"],
                }
                for row in _clean_editor_rows(edited_education).to_dict("records")
            ],
            "publications": [
                {
                    "citation": row["citation"],
                    "authors": [
                        a.strip()
                        for a in (row["authors"] or "").split(",")
                        if a.strip()
                    ],
                    "conference": row["conference"],
                    "year": row["year"],
                }
                for row in _clean_editor_rows(edited_publications).to_dict("records")
            ],
            "qualifications": [
                {"name": row["name"], "year": row["year"]}
                for row in _clean_editor_rows(edited_qualifications).to_dict("records")
            ],
            "projects": [
                {
                    "name": row["name"],
                    "description": row["description"],
                    "tech": [
                        t.strip() for t in (row["tech"] or "").split(",") if t.strip()
                    ],
                    "url": row["url"] or None,
                }
                for row in _clean_editor_rows(edited_projects).to_dict("records")
            ],
            "activities": [
                {
                    "name": row["name"],
                    "organisation": row["organisation"],
                    "start": row["start"],
                    "end": row["end"],
                }
                for row in _clean_editor_rows(edited_activities).to_dict("records")
            ],
        }
        try:
            response = httpx.put(
                f"{_settings.api_base_url}/cv/truth-base",
                json={
                    "extracted_markdown": current["extracted_markdown"],
                    "truth_base": new_truth_base,
                    "label": version_label.strip(),
                },
                timeout=30.0,
            )
            response.raise_for_status()
            st.success(f"Saved as version {response.json()['version']}.")
            st.rerun()
        except httpx.HTTPError as exc:
            st.error(f"Save failed: {exc}")
