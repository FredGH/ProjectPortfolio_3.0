"""Reading the benchmark pool and storing offers (PLAN.md Step 21c)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.offers.benchmark import PoolRow


@dataclass(frozen=True)
class StoredOffer:
    """An offer a user recorded.

    Attributes:
        id: Row id.
        job_group_id: The job it relates to, if any.
        application_id: The application it relates to (filled in once
            `fct_application` exists).
        company: The employer.
        title: The role.
        received_on: When it arrived.
        amount: The amount.
        basis: `daily`, `hourly` or `annual`.
        engagement_type: `contract` or `permanent`.
        ir35_status: `inside`, `outside` or `undetermined`.
        category: The category.
        region: The region.
        seniority: The seniority band.
        notes: Free notes.
        benchmark: The benchmark computed when it was recorded.
        tax_year: The tax config year used for any take-home figure.
        created_at: When it was recorded.
    """

    id: uuid.UUID
    job_group_id: str | None
    application_id: uuid.UUID | None
    company: str | None
    title: str | None
    received_on: date
    amount: float
    basis: str
    engagement_type: str
    ir35_status: str
    category: str
    region: str
    seniority: str
    notes: str | None
    benchmark: dict | None
    tax_year: str | None
    created_at: datetime


def load_pool(engine: Engine) -> list[PoolRow]:
    """Read the benchmark pool.

    Args:
        engine: The app-role engine.

    Returns:
        Every contract and permanent posting of the targeted corpus.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT job_group_id, title, company, category, region, "
                "seniority_band, "
                "contract_type, ir35_status, figure, first_seen_month "
                "FROM gold.offer_benchmark_pool"
            )
        ).all()
    return [
        PoolRow(
            r.job_group_id,
            r.title,
            r.company,
            r.category,
            r.region,
            r.seniority_band,
            r.contract_type,
            r.ir35_status,
            None if r.figure is None else float(r.figure),
            r.first_seen_month,
        )
        for r in rows
    ]


def save_offer(
    engine: Engine,
    user_id: uuid.UUID,
    fields: dict,
    benchmark: dict | None,
    tax_year: str | None,
) -> uuid.UUID:
    """Record an offer with the benchmark computed at the time.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        fields: Offer columns (`amount`, `basis`, `engagement_type`,
            `ir35_status`, `category`, `region`, `seniority`, and optionally
            `job_group_id`, `company`, `title`, `received_on`, `notes`).
        benchmark: The benchmark snapshot, JSON-friendly.
        tax_year: The tax config year used, if any.

    Returns:
        The new offer's id.
    """
    allowed = (
        "job_group_id",
        "company",
        "title",
        "received_on",
        "amount",
        "basis",
        "engagement_type",
        "ir35_status",
        "category",
        "region",
        "seniority",
        "notes",
    )
    values = {k: fields.get(k) for k in allowed if fields.get(k) is not None}
    columns = ", ".join([*values, "user_id", "benchmark", "tax_year"])
    params = ", ".join(f":{k}" for k in values)
    with session_scope(engine, user_id=user_id) as conn:
        return conn.execute(
            text(
                f"INSERT INTO offers.offer_received ({columns}) VALUES "
                f"({params}, :user_id, CAST(:benchmark AS jsonb), :tax_year) "
                "RETURNING id"
            ),
            {
                **values,
                "user_id": user_id,
                "benchmark": json.dumps(benchmark) if benchmark is not None else None,
                "tax_year": tax_year,
            },
        ).scalar_one()


def list_offers(engine: Engine, user_id: uuid.UUID) -> list[StoredOffer]:
    """List a user's recorded offers.

    Args:
        engine: The app-role engine.
        user_id: The owner.

    Returns:
        Their offers, newest first.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(
            text(
                "SELECT id, job_group_id, application_id, company, title, received_on, "
                "amount, basis, engagement_type, ir35_status, category, region, "
                "seniority, notes, benchmark, tax_year, created_at "
                "FROM offers.offer_received ORDER BY created_at DESC, id"
            )
        ).all()
    return [
        StoredOffer(
            r.id,
            r.job_group_id,
            r.application_id,
            r.company,
            r.title,
            r.received_on,
            float(r.amount),
            r.basis,
            r.engagement_type,
            r.ir35_status,
            r.category,
            r.region,
            r.seniority,
            r.notes,
            r.benchmark,
            r.tax_year,
            r.created_at,
        )
        for r in rows
    ]
