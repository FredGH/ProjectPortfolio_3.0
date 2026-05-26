from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.auth.dependencies import UserClaims, get_current_user
from api.auth.rbac import require_role
from db import engine

router = APIRouter(prefix="/fills", tags=["fills"])


class FillSubmit(BaseModel):
    order_id: str = Field(example="ORD123456")
    instrument_id: str = Field(example="AAPL")
    instrument_class: Literal[
        "equity", "equity_future", "fixed_income", "fx_derivative"
    ] = Field(example="equity")
    counterparty_id: str = Field(example="BROKER1")
    side: Literal["BUY", "SELL"] = Field(example="BUY")
    fill_price: float = Field(..., gt=0, example=150.50)
    fill_quantity: int = Field(..., gt=0, example=100)
    venue_id: Optional[str] = Field(None, example="NASDAQ")
    market_impact_bps: Optional[float] = Field(None, example=2.5)
    commission_bps: Optional[float] = Field(None, example=1.0)
    currency: str = Field("EUR", example="USD")


class FillResponse(BaseModel):
    fill_id: str
    order_id: str
    fill_price: float
    fill_quantity: int
    fill_time: datetime


@router.post(
    "",
    response_model=FillResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("TRADER", "HEAD_OF_TRADING", "ADMIN"))],
)
def submit_fill(
    payload: FillSubmit,
    user: UserClaims = Depends(get_current_user),
) -> FillResponse:
    fill_id = str(uuid.uuid4())
    fill_time = datetime.now(timezone.utc)

    sql = sa.text("""
        INSERT INTO stg_raw.rt_fills (
            fill_id, order_id, instrument_id, instrument_class,
            counterparty_id, side, fill_price, fill_quantity,
            venue_id, fill_time, market_impact_bps, commission_bps, currency
        ) VALUES (
            :fill_id, :order_id, :instrument_id, :instrument_class,
            :counterparty_id, :side, :fill_price, :fill_quantity,
            :venue_id, :fill_time, :market_impact_bps, :commission_bps, :currency
        )
    """)

    try:
        with engine.begin() as conn:
            conn.execute(
                sql,
                {
                    "fill_id": fill_id,
                    "order_id": payload.order_id,
                    "instrument_id": payload.instrument_id,
                    "instrument_class": payload.instrument_class,
                    "counterparty_id": payload.counterparty_id,
                    "side": payload.side,
                    "fill_price": payload.fill_price,
                    "fill_quantity": payload.fill_quantity,
                    "venue_id": payload.venue_id,
                    "fill_time": fill_time,
                    "market_impact_bps": payload.market_impact_bps,
                    "commission_bps": payload.commission_bps,
                    "currency": payload.currency,
                },
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return FillResponse(
        fill_id=fill_id,
        order_id=payload.order_id,
        fill_price=payload.fill_price,
        fill_quantity=payload.fill_quantity,
        fill_time=fill_time,
    )
