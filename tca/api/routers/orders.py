from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.auth.dependencies import UserClaims, get_current_user
from api.schemas.models import OrderSummary
from api.services.tca_service import TCAService

router = APIRouter(prefix="/orders", tags=["orders"])
_svc = TCAService()


@router.get("", response_model=list[OrderSummary])
def get_orders(
    user: Annotated[UserClaims, Depends(get_current_user)],
    trade_date: Annotated[str, Query()] = "2025-01-15",
    limit: Annotated[int, Query(le=1000)] = 500,
) -> list[OrderSummary]:
    rows = _svc.get_orders(trade_date, user, limit=limit)
    return [OrderSummary(**r) for r in rows]
