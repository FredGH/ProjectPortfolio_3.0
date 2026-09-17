"""CV extraction pipeline (PLAN.md Step 13): Docling converts a CV
document to markdown; one LLM call (task "cv_extraction", routed to
Ollama only — DECISIONS.md's task split never migrates this task) parses
that markdown into `core.cv.schema.CVTruthBase`. Bullet IDs are computed
here from the LLM's raw bullet text, never trusted from the LLM's own
output — see `core.cv.bullet_id.compute_bullet_id`.
"""

from __future__ import annotations

import io
import json
import re
from functools import lru_cache

from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter
from pydantic import BaseModel

from core.cv.bullet_id import compute_bullet_id
from core.cv.schema import (
    Bullet,
    Certification,
    CVTruthBase,
    Education,
    Experience,
    Project,
    Publication,
    Skill,
)
from core.llm.gateway import complete
from core.llm.prompts import load_prompt
from core.llm.types import LLMAdapter

_PROMPT_FAMILY = "local"
_PROMPT_VERSION_NUMBER = 4


class _RawExperience(BaseModel):
    """One experience entry as the LLM returns it — bullets are plain
    strings here; `extract_truth_base` attaches stable IDs afterward.
    """

    company: str
    title: str
    start: str | None = None
    end: str | None = None
    bullets: list[str] = []
    tech: list[str] = []
    metrics: list[str] = []


class _RawCVTruthBase(BaseModel):
    """The LLM's raw extraction response, before bullet IDs are attached."""

    identity: str
    headline: str
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    nationality: str | None = None
    summary: str | None = None
    locations: list[str] = []
    work_auth: str | None = None
    skills: list[Skill] = []
    experience: list[_RawExperience] = []
    projects: list[Project] = []
    publications: list[Publication] = []
    education: list[Education] = []
    qualifications: list[Certification] = []
    activities_interests: list[str] = []


_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _parse_json_response(text: str) -> dict[str, object]:
    """Parse an LLM response into a JSON dict, tolerating common wrapping.

    Local models routinely don't return bare JSON despite being asked for
    it (all observed from llama3.1:8b on this task, across different
    calls): a ```json ... ``` fence, a fence preceded by explanatory
    prose, or no fence at all with the JSON object embedded in prose.
    Tries, in order: the text as-is; the first fenced code block anywhere
    in the text; the substring from the first "{" to the last "}". Each
    candidate is a plain `json.loads` attempt — a candidate that happens
    to parse but isn't the right shape still fails
    `_RawCVTruthBase.model_validate` afterward, so this never turns a
    genuinely malformed response into a false success.

    Args:
        text: The raw response text, already `.strip()`-ped.

    Returns:
        The parsed JSON value from the first candidate that parses.

    Raises:
        json.JSONDecodeError: If no candidate parses as JSON.
    """
    candidates = [text]
    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        candidates.append(fence_match.group(1).strip())
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        candidates.append(text[brace_start : brace_end + 1])

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    assert last_error is not None  # `candidates` always has >= 1 entry
    raise last_error


def _recover_education_qualification_keyed_as_degree(parsed: dict[str, object]) -> None:
    """Recover an education entry's qualification keyed as "degree" instead.

    Observed from llama3.1:8b on a real CV: instead of the documented
    `"qualification"` key, education entries routinely come back as
    `"degree"` — the model substituting its own natural key for the
    requested one rather than omitting the data. Mutates
    `parsed["education"]` in place; a no-op if "education" is absent
    or entries are already well-formed.

    Args:
        parsed: The LLM response, already parsed as JSON.
    """
    education = parsed.get("education")
    if not isinstance(education, list):
        return
    for entry in education:
        has_degree_not_qualification = (
            isinstance(entry, dict)
            and "qualification" not in entry
            and "degree" in entry
        )
        if has_degree_not_qualification:
            entry["qualification"] = entry.pop("degree")


def _recover_publication_citations(parsed: dict[str, object]) -> None:
    """Recover a publication's citation text keyed as "title" instead.

    Observed from llama3.1:8b on a real CV: instead of the documented
    `{"citation": "..."}` shape, some publication entries came back as
    `{"title": "..."}` with the full citation text under that key —
    the model substituting its own natural key for the requested one
    rather than omitting the data. Mutates `parsed["publications"]` in
    place; a no-op if "publications" is absent or entries are already
    well-formed.

    Args:
        parsed: The LLM response, already parsed as JSON.
    """
    publications = parsed.get("publications")
    if not isinstance(publications, list):
        return
    for entry in publications:
        if isinstance(entry, dict) and "citation" not in entry and "title" in entry:
            entry["citation"] = entry.pop("title")


_COMBINED_YEAR_RANGE_RE = re.compile(r"^(\d{4}(?:-\d{2})?)\s*-\s*(\d{4}(?:-\d{2})?)$")


def _split_combined_education_range(entry: Education) -> Education:
    """Split a "start" field like "2016-2017" into separate start/end.

    Observed from llama3.1:8b on a real CV: a combined date range is
    routinely extracted whole into "start", with "end" left null,
    instead of split per the documented shape. A no-op unless "start"
    is a bare range and "end" wasn't already given.

    Args:
        entry: One raw `Education` entry, as parsed from the LLM
            response.

    Returns:
        `entry` unchanged, or with "start"/"end" split apart.
    """
    if entry.end is None and entry.start is not None:
        match = _COMBINED_YEAR_RANGE_RE.match(entry.start.strip())
        if match:
            return entry.model_copy(
                update={"start": match.group(1), "end": match.group(2)}
            )
    return entry


@lru_cache
def _converter() -> DocumentConverter:
    """Build (once) and cache the shared `DocumentConverter`.

    Constructing a `DocumentConverter` loads layout/table models and can
    trigger a runtime model download, so it must not happen on every
    `docling_to_markdown` call — this lazily-created singleton pays that
    cost once per process.

    Returns:
        The process-wide `DocumentConverter` instance.
    """
    return DocumentConverter()


def docling_to_markdown(file_bytes: bytes, filename: str) -> str:
    """Convert a CV document to markdown via Docling.

    Args:
        file_bytes: The raw document bytes. Production traffic is PDF;
            this module's own tests use HTML instead, since Docling
            also handles it and it avoids needing a synthetic PDF
            fixture.
        filename: The original filename — its extension tells Docling
            which format to parse.

    Returns:
        The document's content as markdown text.
    """
    stream = DocumentStream(name=filename, stream=io.BytesIO(file_bytes))
    result = _converter().convert(stream)
    return result.document.export_to_markdown()


def extract_truth_base(
    markdown: str,
    *,
    adapters: dict[str, LLMAdapter],
    provider: str | None = None,
    model: str | None = None,
    prompt_family: str | None = None,
) -> CVTruthBase:
    """Parse a CV's markdown into a `CVTruthBase` via one LLM call.

    Args:
        markdown: The CV's markdown text (from `docling_to_markdown`,
            or a synthetic snippet in eval/test cases).
        adapters: Every available LLM adapter, keyed by provider.
        provider: Overrides the production-configured provider — used
            by the eval harness to force a specific provider. `None`
            (the default) uses `cv_extraction`'s `config/llm_tasks.yml`
            entry, which is always Ollama (see DECISIONS.md's task
            split — this task never migrates to Anthropic).
        model: The model to use with `provider`. Must be given together
            with `provider`.
        prompt_family: Which prompt file to load — defaults to this
            module's own `_PROMPT_FAMILY` ("local") when `provider` is
            given without an explicit `prompt_family`.

    Returns:
        The parsed `CVTruthBase`, with every bullet's `bullet_id`
        computed deterministically from its own text and position.

    Raises:
        ValueError: If the LLM's response can't be parsed as the
            expected JSON shape.
    """
    resolved_family = prompt_family or _PROMPT_FAMILY
    prompt_template = load_prompt(
        "cv_extraction", resolved_family, _PROMPT_VERSION_NUMBER
    )
    prompt = prompt_template.format(markdown=markdown)
    prompt_version = f"{resolved_family}.v{_PROMPT_VERSION_NUMBER}"
    response = complete(
        task="cv_extraction",
        prompt=prompt,
        prompt_version=prompt_version,
        adapters=adapters,
        provider=provider,
        model=model,
    )
    response_text = response.text.strip()
    try:
        parsed = _parse_json_response(response_text)
        _recover_publication_citations(parsed)
        _recover_education_qualification_keyed_as_degree(parsed)
        raw = _RawCVTruthBase.model_validate(parsed)
    except (json.JSONDecodeError, ValueError) as exc:
        snippet = response_text[:200]
        raise ValueError(
            f"could not parse cv_extraction response: {exc} "
            f"(response started with: {snippet!r})"
        ) from exc

    experience = [
        Experience(
            company=exp.company,
            title=exp.title,
            start=exp.start,
            end=exp.end,
            bullets=[
                Bullet(bullet_id=compute_bullet_id(index, text), text=text)
                for text in exp.bullets
            ],
            tech=exp.tech,
            metrics=exp.metrics,
        )
        for index, exp in enumerate(raw.experience)
    ]
    return CVTruthBase(
        identity=raw.identity,
        headline=raw.headline,
        email=raw.email,
        phone=raw.phone,
        linkedin_url=raw.linkedin_url,
        nationality=raw.nationality,
        summary=raw.summary,
        locations=raw.locations,
        work_auth=raw.work_auth,
        skills=raw.skills,
        experience=experience,
        projects=raw.projects,
        publications=raw.publications,
        education=[_split_combined_education_range(e) for e in raw.education],
        qualifications=raw.qualifications,
        activities_interests=raw.activities_interests,
    )
