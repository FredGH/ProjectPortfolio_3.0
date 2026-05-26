from __future__ import annotations

from contextlib import asynccontextmanager

from arq import create_pool as arq_create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

from agentic_triage import settings
from agentic_triage.api.router import router
from agentic_triage.core.config import DomainConfig
from agentic_triage.db import create_pool as pg_create_pool


def create_multi_domain_app(configs: dict[str, DomainConfig]) -> FastAPI:
    """FastAPI factory that auto-discovers domain configs and registers all routes.

    Args:
        configs: Mapping of domain name → DomainConfig, loaded from domains/*/config.yaml.

    Usage::

        configs = load_configs()           # see scripts/run_api.py
        app = create_multi_domain_app(configs)
        uvicorn.run(app, host="0.0.0.0", port=8000)
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.redis = await arq_create_pool(
            RedisSettings(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
            )
        )
        app.state.db = await pg_create_pool()
        app.state.configs = configs
        yield
        await app.state.redis.close()
        await app.state.db.close()

    app = FastAPI(
        title="Agentic Triage API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app
