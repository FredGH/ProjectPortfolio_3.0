from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter

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


def create_app() -> FastAPI:
    app = FastAPI(
        title="PrivateBank TCA API",
        description="Transaction Cost Analysis platform — pan-European equities, MiFID II compliant.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:4200"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = APIRouter(prefix="/api")
    api.include_router(auth.router)
    api.include_router(tca.router)
    api.include_router(orders.router)
    api.include_router(reports.router)
    api.include_router(mifid.router)
    api.include_router(pipeline.router)
    api.include_router(fills.router)
    api.include_router(predict.router)
    api.include_router(regime.router)
    app.include_router(api)

    @app.get("/health", include_in_schema=False)
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
