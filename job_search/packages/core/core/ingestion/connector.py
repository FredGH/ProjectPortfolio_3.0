"""The Connector protocol every source implements (PLAN.md Step 3).

Adding a connector means writing one class satisfying this Protocol plus
one config block in config/sources.yml — the shared runner never changes.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from typing import Protocol

from core.ingestion.raw_job import RawJob


class Connector(Protocol):
    """Structural interface: anything with a matching fetch() qualifies."""

    def fetch(
        self, query: object, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Fetch job postings matching `query`, updated since `since`.

        Args:
            query: Connector-specific query — a search string for an API
                connector, a `ManualJobQuery` for manual entry. Each
                connector defines and documents its own concrete type.
            since: Only return postings updated at or after this time, for
                connectors that support incremental fetching. `None` means
                "no incremental filter" — a full fetch.
            run_id: The ULID identifying this run, assigned once by the
                runner and stamped onto every yielded RawJob — connectors
                never generate their own run_id.

        Yields:
            One `RawJob` per matching posting.
        """
        ...
