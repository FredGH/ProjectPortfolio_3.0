from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.auth.jwt_handler import load_keys
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_keys()
    yield


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

    @app.get("/health", include_in_schema=False)
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
