"""Where a rendered CV lives on disk, and the job spec saved beside it.

Every CV goes to `<root>/<first_last>/<YYYY-MM>/<job_title>/<company>/`, so
CVs for the same person, month, title and employer sit together and a job's
spec travels with its CV.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.render.format import _slug
from core.tailoring.schema import JobContext, JobSource

JOB_SPEC_FILENAME = "job_spec.md"
"""The job spec's name inside a CV directory."""

_MAX_PART_CHARS = 60


def _part(value: str | None, fallback: str) -> str:
    """Make one directory name: lower-case, underscores, never empty.

    Args:
        value: The raw text (a name, a title, a company).
        fallback: Used when nothing usable is left.

    Returns:
        A short filesystem-safe name.
    """
    slug = _slug(value or "").lower()[:_MAX_PART_CHARS].rstrip("_")
    return slug or fallback


def cv_directory(
    root: Path,
    identity: str,
    generated_at: datetime,
    title: str,
    company: str | None,
) -> Path:
    """Work out the directory for one CV.

    Args:
        root: The output root (for example `output`).
        identity: The candidate's full name (`firstname lastname`).
        generated_at: When the CV was generated.
        title: The job title (`title_for_display`).
        company: The employer, or None.

    Returns:
        `<root>/<first_last>/<YYYY-MM>/<job_title>/<company>`; nothing is
        created. A missing company is `unknown_company`.
    """
    return (
        root
        / _part(identity, "unknown_candidate")
        / generated_at.strftime("%Y-%m")
        / _part(title, "untitled")
        / _part(company, "unknown_company")
    )


def job_ui_link(base_url: str, job_group_id: str) -> str:
    """Build the link to a job in the Tailored CV Review page of the UI.

    Args:
        base_url: Where the UI runs, for example `http://localhost:8501`.
        job_group_id: The job's id.

    Returns:
        A URL that opens the page with the job pre-selected (when it is
        among the user's top-scored jobs).
    """
    return f"{base_url.rstrip('/')}/Tailored_CV_Review?job={job_group_id}"


def render_job_spec(job: JobContext, source: JobSource, ui_link: str) -> str:
    """Write the job spec as Markdown.

    Args:
        job: The job's title, company, description and skills.
        source: Where the posting came from.
        ui_link: The link to the job in the UI.

    Returns:
        The Markdown text with a trailing newline.
    """
    heading = " — ".join(
        p.strip() for p in (job.title_for_display, job.company) if p and p.strip()
    )
    urls = list(source.source_urls)
    if source.apply_url and source.apply_url not in urls:
        urls.insert(0, source.apply_url)
    lines = [f"# {heading or 'Job spec'}", ""]
    lines.append(f"- **Job posting:** {urls[0] if urls else 'not recorded'}")
    for other in urls[1:]:
        lines.append(f"- **Also seen at:** {other}")
    if source.source_names:
        lines.append(f"- **Source:** {', '.join(source.source_names)}")
    if source.location:
        lines.append(f"- **Location:** {source.location}")
    if source.posted_at:
        lines.append(f"- **Posted:** {source.posted_at}")
    lines.append(f"- **In the UI:** {ui_link}")
    lines.append(f"- **Job id:** {job.job_group_id}")
    for level, label in (("must_have", "Must have"), ("nice_to_have", "Nice to have")):
        names = [s.label for s in job.skills if s.requirement_level == level]
        if names:
            lines += ["", f"## Skills — {label}", "", ", ".join(names)]
    unrated = [
        s.label
        for s in job.skills
        if s.requirement_level not in ("must_have", "nice_to_have")
    ]
    if unrated:
        lines += ["", "## Skills — Other", "", ", ".join(unrated)]
    lines += ["", "## Description", "", job.description.strip() or "(none recorded)"]
    return "\n".join(lines) + "\n"
