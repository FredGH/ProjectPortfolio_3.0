"""Seed target_company with Greenhouse companies, verified live.

Usage: python3.11 scripts/seed_target_company.py

Tries each (name, board_slug) candidate below against Greenhouse's real
public board API and upserts only the ones that return a real board (HTTP
200 with a `jobs` key) — see the docstring on this module in the
implementation plan for why this isn't a blind data insert. Run from
job_search/ with Postgres up and .env's DATABASE_URL/APP_DATABASE_URL
pointed at a reachable Postgres (localhost outside Docker).
"""

from __future__ import annotations

import sys

import httpx

sys.path.insert(0, "packages/core")

from core.db.session import build_engine  # noqa: E402
from core.db.target_company import upsert_target_company  # noqa: E402
from core.settings import get_settings  # noqa: E402

# Best-effort candidates — commonly cited as Greenhouse users, but the
# exact board_slug is unverified until this script actually checks it
# live. Extend this list with more candidates as they're researched; a
# wrong guess here just gets reported as NOT FOUND, never inserted.
_CANDIDATES: list[tuple[str, str]] = [
    ("Airbnb", "airbnb"),
    ("Stripe", "stripe"),
    ("Robinhood", "robinhood"),
    ("Coinbase", "coinbase"),
    ("DoorDash", "doordash"),
    ("Notion", "notion"),
    ("Figma", "figma"),
    ("Discord", "discord"),
    ("Instacart", "instacart"),
    ("Reddit", "reddit"),
    ("Asana", "asana"),
    ("Pinterest", "pinterest"),
    ("Anthropic", "anthropic"),
    ("Linear", "linear"),
    ("Vercel", "vercel"),
    ("Ramp", "ramp"),
    ("Brex", "brex"),
    ("Scale AI", "scaleai"),
    ("Plaid", "plaid"),
    ("Rippling", "rippling"),
]


def _board_is_real(client: httpx.Client, board_slug: str) -> bool:
    """Check whether a Greenhouse board slug resolves to a real board.

    Args:
        client: The HTTP client to check with.
        board_slug: The candidate board token.

    Returns:
        True if the board endpoint returns HTTP 200 with a `jobs` key.
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs"
    try:
        response = client.get(url, timeout=10.0)
        return response.status_code == 200 and "jobs" in response.json()
    except (httpx.HTTPError, ValueError):
        return False


def main() -> int:
    """Check every candidate live and upsert the ones that resolve.

    Returns:
        0 always — a candidate not resolving is reported, not an error.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)

    kept: list[str] = []
    dropped: list[str] = []
    with httpx.Client() as client:
        for name, board_slug in _CANDIDATES:
            if _board_is_real(client, board_slug):
                kept.append(f"{name} ({board_slug})")
                with engine.connect() as conn:
                    upsert_target_company(
                        conn,
                        name=name,
                        ats_provider="greenhouse",
                        board_slug=board_slug,
                    )
                    conn.commit()
            else:
                dropped.append(f"{name} ({board_slug})")

    print(f"Seeded {len(kept)} verified companies:")
    for line in kept:
        print(f"  OK   {line}")
    print(f"Dropped {len(dropped)} unverified candidates (not a real board):")
    for line in dropped:
        print(f"  MISS {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
