from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth.dependencies import get_current_user
from api.auth.rbac import require_role
from db import engine

router = APIRouter(prefix="/predict", tags=["predict"])


class PredictRequest(BaseModel):
    instrument_class: Literal[
        "equity", "equity_future", "fixed_income", "fx_derivative"
    ] = Field(example="equity")
    side: Literal["BUY", "SELL"] = Field(example="BUY")
    quantity: int = Field(..., gt=0, example=1000)
    vol_regime: Literal["LOW", "MEDIUM", "HIGH"] = Field(
        default="MEDIUM", example="MEDIUM"
    )
    algo_id: Optional[str] = Field(None, example="TWAP")
    venue_id: Optional[str] = Field(None, example="NYSE")
    order_hour: int = Field(default=10, ge=0, le=23, example=10)
    order_dow: int = Field(default=2, ge=0, le=6, example=2)


class PredictResponse(BaseModel):
    instrument_class: str
    predicted_slippage_bps: float
    ci_low_bps: float
    ci_high_bps: float
    trained_on: int
    feature_importance: dict[str, float]


@router.post(
    "/slippage",
    response_model=PredictResponse,
    dependencies=[Depends(get_current_user)],
)
def predict_slippage(payload: PredictRequest) -> Any:
    from analytics.modules.execution_quality_predictor import predict

    result = predict(
        instrument_class=payload.instrument_class,
        side=payload.side,
        quantity=payload.quantity,
        vol_regime=payload.vol_regime,
        algo_id=payload.algo_id or "UNKNOWN",
        venue_id=payload.venue_id or "UNKNOWN",
        order_hour=payload.order_hour,
        order_dow=payload.order_dow,
    )
    if "error" in result:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post(
    "/train",
    dependencies=[Depends(require_role("ADMIN"))],
)
def train_models() -> dict[str, Any]:
    from analytics.modules.execution_quality_predictor import train

    return train(engine)


@router.get(
    "/status",
    dependencies=[Depends(get_current_user)],
)
def model_status() -> dict[str, Any]:
    from analytics.modules.execution_quality_predictor import model_status

    return model_status()
