from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.auth.jwt_handler import load_keys
from api.routers import auth, fills, mifid, orders, pipeline, predict, regime, reports, tca


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

    app.include_router(auth.router)
    app.include_router(tca.router)
    app.include_router(orders.router)
    app.include_router(reports.router)
    app.include_router(mifid.router)
    app.include_router(pipeline.router)
    app.include_router(fills.router)
    app.include_router(predict.router)
    app.include_router(regime.router)

    @app.get("/health", include_in_schema=False)
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app
