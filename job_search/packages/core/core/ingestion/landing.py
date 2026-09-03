"""The immutable landing zone (PLAN.md Step 2).

One JSONL line per record, gzip-compressed, at a fixed
`source=.../dt=.../run_id=.../part-0001.jsonl.gz` path. Immutable and
replayable — bronze can always be rebuilt from here without re-hitting a
single API.
"""

from __future__ import annotations

import datetime
import gzip
import json

import fsspec


def write_landing_record(
    landing_uri: str,
    *,
    source_name: str,
    run_id: str,
    record: dict[str, object],
    fetched_at: datetime.datetime,
) -> str:
    """Write one record as a gzip JSONL file in the landing zone.

    Args:
        landing_uri: Root URI of the landing zone (`file://...` locally,
            `gs://...` in GCP — this function doesn't branch on which).
        source_name: The source this record came from, e.g.
            "linkedin_manual".
        run_id: The ULID identifying this ingestion run.
        record: The JSON-serialisable record to write, verbatim.
        fetched_at: When this record was captured — determines the `dt=`
            partition.

    Returns:
        The full path written to.
    """
    dt = fetched_at.date().isoformat()
    path = (
        f"{landing_uri.rstrip('/')}/source={source_name}/dt={dt}/"
        f"run_id={run_id}/part-0001.jsonl.gz"
    )
    with fsspec.open(path, "wb") as raw_handle:
        with gzip.GzipFile(fileobj=raw_handle, mode="wb") as gz_handle:
            gz_handle.write((json.dumps(record) + "\n").encode())
    return path
