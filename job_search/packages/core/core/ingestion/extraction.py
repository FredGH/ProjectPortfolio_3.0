"""LLM structured extraction from a pasted job spec (PLAN.md Step 2).

The parse is a derived field, never authoritative over the raw text — if
extraction is wrong, it's re-run from landing, never re-requested from the
user. Extraction failures here propagate; it's the orchestration layer's
job (core.ingestion.manual) to decide that a failed parse should not fail
the whole ingest request.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from core.llm.gateway import complete
from core.llm.types import LLMAdapter

_PROMPT_TEMPLATE = """Extract the following fields from this job posting as \
a JSON object with exactly these keys: title, company, location, contract, \
salary, seniority. Use null for any field not stated in the text. Respond \
with JSON only, no other text.

Job posting:
{raw_text}
"""


class ExtractedJobFields(BaseModel):
    """Structured fields parsed from a job spec's raw text.

    Attributes:
        title: The job title, as stated.
        company: The employer name, as stated.
        location: The stated location.
        contract: The engagement type as stated in free text (e.g.
            "permanent", "contract") — the structured `engagement_type`
            enum lives in Step 5a, not here.
        salary: The stated salary/rate, as free text.
        seniority: The stated seniority level.
    """

    title: str | None = None
    company: str | None = None
    location: str | None = None
    contract: str | None = None
    salary: str | None = None
    seniority: str | None = None


def extract_job_fields(
    raw_text: str,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
    prompt_version: str = "local.v1",
) -> ExtractedJobFields:
    """Extract structured fields from a job spec's raw text via the LLM gateway.

    Args:
        raw_text: The verbatim pasted job spec.
        adapters: Every available adapter, keyed by provider name.
        config_path: Path to the task-config YAML. Defaults to
            `config/llm_tasks.yml` at the repository root.
        prompt_version: The versioned prompt identifier for the call log.

    Returns:
        The parsed `ExtractedJobFields`.

    Raises:
        Exception: Whatever the underlying adapter or JSON parsing raises.
            Not caught here — callers decide whether a failure should be
            fatal (see core.ingestion.manual.ingest_manual_job).
    """
    response = complete(
        "manual_entry_parse",
        _PROMPT_TEMPLATE.format(raw_text=raw_text),
        prompt_version=prompt_version,
        adapters=adapters,
        config_path=config_path,
    )
    return ExtractedJobFields.model_validate_json(response.text)


def apply_user_overrides(
    extracted: ExtractedJobFields,
    overrides: dict[str, str | None],
) -> tuple[ExtractedJobFields, dict[str, str]]:
    """Merge user-supplied field overrides over the parsed extraction.

    Args:
        extracted: The LLM's parsed fields.
        overrides: User-supplied values for `company`, `title`, and
            `location` — `None` means "no override for this field".

    Returns:
        A tuple of (merged fields, field_source), where field_source maps
        every field name that was actually overridden to `"user"`. Fields
        the user didn't override are absent from field_source (their
        source is implicitly the parser).
    """
    field_source: dict[str, str] = {}
    merged = extracted.model_copy()
    for field_name, value in overrides.items():
        if value:
            setattr(merged, field_name, value)
            field_source[field_name] = "user"
    return merged, field_source
