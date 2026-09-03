"""The RawJob envelope — the one shape every connector yields (PLAN.md Step 3).

source_name and job_url are captured at extraction time, before any
parsing, so provenance survives even when a source's payload shape
changes or downstream parsing fails.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class RawJob:
    """One fetched job posting, in the shape every connector must produce.

    Attributes:
        source_name: Which source this came from, e.g. "adzuna" or a
            user-typed label for manual entries, e.g. "linkedin_manual".
        source_job_id: The source's own identifier for this posting, or a
            content hash where no stable identifier exists.
        job_url: The original (uncanonicalised) job URL.
        job_url_canonical: The canonicalised job URL.
        payload: The full record payload — untouched raw content plus
            whatever derived fields the connector chooses to attach.
        fetched_at: When this record was captured.
        run_id: The ULID identifying the run that produced this record —
            shared by every RawJob yielded in the same run_connector()
            call, never generated per-item.
        request_params: Whatever request parameters produced this record
            (empty for manual entry).
        payload_sha256: SHA-256 hex digest of the payload's dedup-relevant
            content — the runner's landing/bronze writes key on this.
    """

    source_name: str
    source_job_id: str
    job_url: str
    job_url_canonical: str
    payload: dict[str, object]
    fetched_at: datetime.datetime
    run_id: str
    request_params: dict[str, object]
    payload_sha256: str
