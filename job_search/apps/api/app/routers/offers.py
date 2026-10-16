"""Offer-check endpoints (PLAN.md Step 21c).

Checking an offer never saves it; saving records the offer together with the
benchmark computed at that moment. Every answer states `n_disclosed` and the
disclosure rate, and refuses a percentile when the slice is too thin.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from core.db.session import get_current_user_id
from core.offers import service, store
from core.offers.benchmark import Offer
from core.offers.config import load_offers_config
from core.offers.tax import load_tax_config

router = APIRouter()


class OfferRequest(BaseModel):
    """An offer to check.

    Attributes:
        amount: The amount offered (positive).
        basis: `daily`, `hourly` or `annual`.
        engagement_type: `contract` or `permanent`.
        ir35_status: `inside`, `outside` or `undetermined`.
        category: The category (for example `data_engineer`).
        region: The region (`unknown` skips the region filter).
        seniority_band: The seniority band (`unknown` skips the filter).
    """

    amount: float = Field(gt=0)
    basis: Literal["daily", "hourly", "annual"]
    engagement_type: Literal["contract", "permanent"]
    ir35_status: Literal["inside", "outside", "undetermined"] = "undetermined"
    category: str = Field(min_length=1, max_length=80)
    region: str = Field(default="unknown", max_length=40)
    seniority_band: str = Field(default="unknown", max_length=40)

    def to_offer(self) -> Offer:
        """Convert to the core `Offer`.

        Returns:
            The offer.
        """
        return Offer(
            self.amount,
            self.basis,
            self.engagement_type,
            self.ir35_status,
            self.category,
            self.region,
            self.seniority_band,
        )


class SaveOfferRequest(OfferRequest):
    """An offer to check and record.

    Attributes:
        company: The employer.
        title: The role.
        received_on: When it arrived (default today).
        notes: Free notes.
        job_group_id: The related job, if any.
    """

    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    received_on: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    job_group_id: str | None = Field(default=None, max_length=100)


class DayRate(BaseModel):
    """A day rate and its IR35 status.

    Attributes:
        day_rate: GBP per day (positive).
        ir35_status: `inside` or `outside`.
    """

    day_rate: float = Field(gt=0)
    ir35_status: Literal["inside", "outside"]


class NetCompareRequest(BaseModel):
    """Two offers to compare on take-home.

    Attributes:
        first: The first offer's day rate and status.
        second: The second offer's.
    """

    first: DayRate
    second: DayRate


def _check(engine: Engine, body: OfferRequest) -> dict[str, Any]:
    """Check an offer against the pool.

    Args:
        engine: The app-role engine.
        body: The offer.

    Returns:
        The benchmark and the take-home estimate.

    Raises:
        HTTPException: 422 when the basis does not fit the engagement.
    """
    try:
        return service.check_offer(
            body.to_offer(),
            store.load_pool(engine),
            load_offers_config(),
            load_tax_config(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/offers/benchmark")
def benchmark_offer(
    body: OfferRequest, engine: Engine = Depends(get_app_db_engine)
) -> dict[str, Any]:
    """Place an offer in the market without saving it.

    Args:
        body: The offer.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The benchmark (percentile only when the slice is not too thin, always
        with n_disclosed and the disclosure rate) and the take-home estimate.
    """
    return _check(engine, body)


@router.post("/offers")
def save_offer(
    body: SaveOfferRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> dict[str, Any]:
    """Check an offer and record it with its benchmark.

    Args:
        body: The offer and its details.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The saved offer's id plus the benchmark and take-home estimate.
    """
    result = _check(engine, body)
    tax_year = result["net"]["tax_year"] if result["net"] else None
    offer_id = store.save_offer(
        engine,
        user_id,
        {
            "amount": body.amount,
            "basis": body.basis,
            "engagement_type": body.engagement_type,
            "ir35_status": body.ir35_status,
            "category": body.category,
            "region": body.region,
            "seniority": body.seniority_band,
            "company": body.company,
            "title": body.title,
            "received_on": body.received_on,
            "notes": body.notes,
            "job_group_id": body.job_group_id,
        },
        result["benchmark"],
        tax_year,
    )
    return {"id": str(offer_id), **result}


@router.get("/offers")
def list_offers(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> list[dict[str, Any]]:
    """List the user's recorded offers.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Their offers, newest first, each with the benchmark it was recorded with.
    """
    return [
        {
            "id": str(o.id),
            "company": o.company,
            "title": o.title,
            "received_on": o.received_on.isoformat(),
            "amount": o.amount,
            "basis": o.basis,
            "engagement_type": o.engagement_type,
            "ir35_status": o.ir35_status,
            "category": o.category,
            "region": o.region,
            "seniority": o.seniority,
            "notes": o.notes,
            "benchmark": o.benchmark,
            "tax_year": o.tax_year,
            "job_group_id": o.job_group_id,
        }
        for o in store.list_offers(engine, user_id)
    ]


@router.post("/offers/net-compare")
def net_compare(body: NetCompareRequest) -> dict[str, Any]:
    """Compare the estimated take-home of two day rates.

    Args:
        body: Two day rates with their IR35 status.

    Returns:
        Both estimates (with their assumptions and the tax year) and the
        difference in take-home.
    """
    return service.compare_net(
        (body.first.day_rate, body.first.ir35_status),
        (body.second.day_rate, body.second.ir35_status),
        load_tax_config(),
    )
