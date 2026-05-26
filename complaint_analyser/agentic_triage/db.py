from __future__ import annotations

import asyncpg

from agentic_triage import settings


async def create_pool(min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        settings.DATABASE_URL,
        min_size=min_size,
        max_size=max_size,
    )
