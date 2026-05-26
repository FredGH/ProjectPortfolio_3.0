from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Config:
    database_url: str
    redis_url: str
    jwt_algorithm: str
    jwt_access_expire_hours: int
    jwt_refresh_expire_days: int
    jwt_private_key_path: str | None
    jwt_public_key_path: str | None
    trade_date: date = field(default_factory=lambda: date(2025, 1, 15))
    # European session: 08:00–16:30 CET = 07:00–15:30 UTC
    session_start_hour_utc: int = 7
    session_end_hour_utc: int = 15
    session_end_minute_utc: int = 30


def get_config() -> Config:
    return Config(
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        jwt_algorithm=os.environ.get("JWT_ALGORITHM", "RS256"),
        jwt_access_expire_hours=int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_HOURS", "8")),
        jwt_refresh_expire_days=int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7")),
        jwt_private_key_path=os.environ.get("JWT_PRIVATE_KEY_PATH"),
        jwt_public_key_path=os.environ.get("JWT_PUBLIC_KEY_PATH"),
    )
