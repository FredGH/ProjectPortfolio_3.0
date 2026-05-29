from __future__ import annotations

import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from api.auth.jwt_handler import load_keys
from api.logging_config import configure_logging
from api.routers import (
    auth,
    fills,
    mifid,
    orders,
    pipeline,
    predict,
    regime,
    reports,
    tca,
)

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    load_keys()
    log.info("api_startup", version="1.0.0")
    yield
    log.info("api_shutdown")


def _cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "http://localhost:4200")
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app() -> FastAPI:
    app = FastAPI(
        title="PrivateBank TCA API",
        description="Transaction Cost Analysis platform — pan-European equities, MiFID II compliant.",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    origins = _cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/api")
    app.include_router(tca.router, prefix="/api")
    app.include_router(orders.router, prefix="/api")
    app.include_router(reports.router, prefix="/api")
    app.include_router(mifid.router, prefix="/api")
    app.include_router(pipeline.router, prefix="/api")
    app.include_router(fills.router, prefix="/api")
    app.include_router(predict.router, prefix="/api")
    app.include_router(regime.router, prefix="/api")

    @app.middleware("http")
    async def log_requests(request: Request, call_next: Callable) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        # Skip health-check spam from ALB probes
        if request.url.path != "/health":
            log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=elapsed_ms,
            )
        return response

    @app.get("/health", include_in_schema=False)
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
