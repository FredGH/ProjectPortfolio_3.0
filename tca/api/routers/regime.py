from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.auth.dependencies import UserClaims, get_current_user
from api.auth.rbac import require_role
from db import engine

router = APIRouter(prefix="/regime", tags=["regime"])


def _import_detector():
    from analytics.modules.regime_detection import (
        detect,
        model_status,
        summary,
        timeline,
        train,
    )
    return detect, model_status, summary, timeline, train


@router.get("/status", dependencies=[Depends(get_current_user)])
def get_status() -> dict[str, Any]:
    _, model_status, *_ = _import_detector()
    return model_status()


@router.post("/train", dependencies=[Depends(require_role("ADMIN"))])
def train_model() -> dict[str, Any]:
    _, _, _, _, train = _import_detector()
    try:
        return train(engine)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.get("/summary")
def get_summary(
    user: Annotated[UserClaims, Depends(get_current_user)],
    trade_date: Annotated[str, Query()] = "2025-01-15",
) -> list[dict[str, Any]]:
    _, _, summary, *_ = _import_detector()
    try:
        return summary(engine, trade_date)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.get("/detect")
def get_detect(
    user: Annotated[UserClaims, Depends(get_current_user)],
    trade_date: Annotated[str, Query()] = "2025-01-15",
    instrument_id: Annotated[str | None, Query()] = None,
    sample_size: Annotated[int, Query(le=500)] = 300,
) -> list[dict[str, Any]]:
    detect, *_ = _import_detector()
    try:
        import pandas as pd

        df = detect(engine, trade_date)
        if df.empty:
            return []
        if instrument_id:
            df = df[df["instrument_id"] == instrument_id]
        if len(df) > sample_size:
            df = df.sample(n=sample_size, random_state=42)
        cols = [
            "bar_id", "instrument_id", "regime", "cluster_id",
            "intraday_vol", "volume_ratio", "momentum", "cluster_confidence",
        ]
        out = df[[c for c in cols if c in df.columns]].copy()
        return out.to_dict(orient="records")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.get("/timeline")
def get_timeline(
    user: Annotated[UserClaims, Depends(get_current_user)],
    trade_date: Annotated[str, Query()] = "2025-01-15",
    instrument_id: Annotated[str, Query()] = "EQTY-001",
) -> list[dict[str, Any]]:
    _, _, _, timeline, _ = _import_detector()
    try:
        return timeline(engine, trade_date, instrument_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
