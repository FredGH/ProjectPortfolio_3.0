"""Run-id generation — a sortable ULID, shared by every ingestion path."""

from __future__ import annotations

from ulid import ULID


def generate_run_id() -> str:
    """Generate a new run id.

    Returns:
        A 26-character Crockford-base32 ULID string — lexicographically
        sortable by creation time, matching the `run_id=01J...` format in
        PLAN.md's landing-zone path convention.
    """
    return str(ULID())
