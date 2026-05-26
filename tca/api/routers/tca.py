from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.auth.dependencies import UserClaims, get_current_user
from api.schemas.models import (AlgoPerformance, AlphaDecayCurve,
                                PeerBenchmark, TCAResult)
from api.services.tca_service import TCAService

router = APIRouter(prefix="/tca", tags=["tca"])
_svc = TCAService()


@router.get("/order/{order_id}", response_model=TCAResult)
def get_order_tca(
    order_id: str,
    user: Annotated[UserClaims, Depends(get_current_user)],
) -> TCAResult:
    result = _svc.get_order_tca(order_id, user)
    if result is None:
        # HTTP 404 (not 403) to prevent existence leakage for CLIENT role
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return TCAResult(**result)


@router.get("/summary", response_model=list[TCAResult])
def get_tca_summary(
    user: Annotated[UserClaims, Depends(get_current_user)],
    trade_date: Annotated[str, Query()] = "2025-01-15",
    instrument_class: Annotated[str | None, Query()] = None,
    counterparty_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(le=1000)] = 200,
) -> list[TCAResult]:
    if user.role == "CLIENT":
        counterparty_id = None  # JWT counterparty_id overrides any requested value
    rows = _svc.get_tca_summary(trade_date, user, counterparty_id, instrument_class, limit)
    return [TCAResult(**r) for r in rows]


@router.get("/algo-performance", response_model=list[AlgoPerformance])
def get_algo_performance(
    user: Annotated[UserClaims, Depends(get_current_user)],
    trade_date: Annotated[str, Query()] = "2025-01-15",
    instrument_class: Annotated[str | None, Query()] = None,
) -> list[AlgoPerformance]:
    if user.role == "CLIENT":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised")
    rows = _svc.get_algo_performance(trade_date, user, instrument_class)
    return [AlgoPerformance(**r) for r in rows]


@router.get("/alpha-decay", response_model=list[AlphaDecayCurve])
def get_alpha_decay(
    user: Annotated[UserClaims, Depends(get_current_user)],
    trade_date: Annotated[str, Query()] = "2025-01-15",
) -> list[AlphaDecayCurve]:
    rows = _svc.get_alpha_decay(trade_date, user)
    return [AlphaDecayCurve(**r) for r in rows]


@router.get("/peer-benchmark/{order_id}", response_model=PeerBenchmark)
def get_peer_benchmark(
    order_id: str,
    user: Annotated[UserClaims, Depends(get_current_user)],
) -> PeerBenchmark:
    result = _svc.get_peer_benchmark(order_id, user)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return PeerBenchmark(**result)
