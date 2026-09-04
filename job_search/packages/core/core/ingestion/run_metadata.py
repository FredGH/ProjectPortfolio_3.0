"""Run metadata emission (PLAN.md Step 3): run_id, source, query, records,
started_at, finished_at, status — one JSON file per run, in the landing
zone alongside the data it describes.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import asdict, dataclass

import fsspec


@dataclass(frozen=True)
class RunMetadata:
    """Summary of one connector run.

    Attributes:
        run_id: The ULID identifying this run.
        source_name: The connector key this run was for, e.g. "adzuna"
            or "manual".
        collection_channel: "targeted" or "discovery" — the channel this
            entire run was collected under. Unlike bronze.raw_jobs's own
            collection_channel column (which is overwritten on a merge if
            the same posting is later captured under a different
            channel), this is an immutable, per-run record: one JSON file
            per run_id, never rewritten.
        query: A string representation of the query used — kept as a
            plain string rather than a generic serialisable type, since
            every connector's query shape differs.
        records: How many RawJobs this run produced.
        started_at: When the run began.
        finished_at: When the run ended (success or failure).
        status: `"success"` or `"failed"`.
    """

    run_id: str
    source_name: str
    query: str
    records: int
    started_at: datetime.datetime
    finished_at: datetime.datetime
    status: str
    collection_channel: str = "targeted"


def write_run_metadata(landing_uri: str, metadata: RunMetadata) -> str:
    """Write one run's metadata as JSON in the landing zone.

    Args:
        landing_uri: Root URI of the landing zone.
        metadata: The `RunMetadata` to write.

    Returns:
        The full path written to.
    """
    path = (
        f"{landing_uri.rstrip('/')}/_runs/{metadata.source_name}/"
        f"{metadata.run_id}.json"
    )
    record = asdict(metadata)
    record["started_at"] = metadata.started_at.isoformat()
    record["finished_at"] = metadata.finished_at.isoformat()
    with fsspec.open(path, "wt") as handle:
        handle.write(json.dumps(record))
    return path
