from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_seconds: int = Field(default=8 * 3600)


class TCAResult(BaseModel):
    order_id: str
    instrument_id: str
    instrument_class: str
    counterparty_id: str
    side: str
    order_type: str
    quantity: int
    filled_quantity: int
    arrival_price: float
    avg_fill_price: Optional[float] = None
    arrival_slippage_bps: Optional[float] = None
    market_impact_bps: Optional[float] = None
    commission_bps: Optional[float] = None
    total_cost_bps: Optional[float] = None
    vwap_slippage_bps: Optional[float] = None
    twap_slippage_bps: Optional[float] = None
    close_slippage_bps: Optional[float] = None
    execution_quality: Optional[str] = None
    vol_regime: Optional[str] = None
    alpha_t30m_bps: Optional[float] = None
    alpha_close_bps: Optional[float] = None
    algo_id: Optional[str] = None
    venue_id: Optional[str] = None
    trader_id: Optional[str] = None
    pre_trade_waiver_type: Optional[str] = None
    post_trade_deferral_type: Optional[str] = None
    settlement_date: Optional[date] = None
    trade_date: Optional[date] = None
    order_time: Optional[datetime] = None


class OrderSummary(BaseModel):
    order_id: str
    instrument_id: str
    instrument_class: str
    counterparty_id: str
    side: str
    order_type: str
    quantity: int
    filled_quantity: Optional[int] = None
    arrival_price: float
    avg_fill_price: Optional[float] = None
    arrival_slippage_bps: Optional[float] = None
    total_cost_bps: Optional[float] = None
    execution_quality: Optional[str] = None
    status: Optional[str] = None
    algo_id: Optional[str] = None
    venue_id: Optional[str] = None
    trader_id: Optional[str] = None
    trade_date: Optional[date] = None
    order_time: Optional[datetime] = None


class AlgoPerformance(BaseModel):
    algo_id: Optional[str] = None
    instrument_class: str
    order_count: int
    avg_arrival_slippage_bps: Optional[float] = None
    avg_vwap_slippage_bps: Optional[float] = None
    avg_market_impact_bps: Optional[float] = None
    avg_participation_rate: Optional[float] = None
    algo_rank: Optional[int] = None


class AlphaDecayCurve(BaseModel):
    vol_regime: str
    instrument_class: str
    order_count: int
    alpha_t30m_bps: Optional[float] = None
    alpha_t1h_bps: Optional[float] = None
    alpha_t4h_bps: Optional[float] = None
    alpha_close_bps: Optional[float] = None
    alpha_decay_rate: Optional[float] = None


class PeerBenchmark(BaseModel):
    order_id: str
    instrument_id: str
    arrival_price: float
    avg_fill_price: Optional[float] = None
    vwap_price: Optional[float] = None
    twap_price: Optional[float] = None
    close_price: Optional[float] = None
    arrival_slippage_bps: Optional[float] = None
    vwap_slippage_bps: Optional[float] = None
    twap_slippage_bps: Optional[float] = None
    close_slippage_bps: Optional[float] = None


class ObsWarning(BaseModel):
    id: int
    check_name: Optional[str] = None
    affected_table: Optional[str] = None
    affected_rows: Optional[int] = None
    warn_value: Optional[str] = None
    warn_time: Optional[datetime] = None


class MifidRow(BaseModel):
    order_id: str
    instrument_id: str
    instrument_class: str
    side: str
    total_quantity: Optional[int] = None
    notional_eur: Optional[float] = None
    venue_id: Optional[str] = None
    waiver_type: Optional[str] = None
    is_lrgs_deferral: Optional[bool] = None
    rts27_category: Optional[str] = None
    trade_date: Optional[date] = None
