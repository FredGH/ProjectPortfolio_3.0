from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BatchItem(BaseModel):
    input_id: str
    text: str


class BatchSubmitResponse(BaseModel):
    batch_id: str
    enqueued: int
    already_done: int


class BatchStatusResponse(BaseModel):
    total: int
    done: int
    failed: int
    completed_at: datetime | None = None


class ReportRequest(BaseModel):
    batch_id: str


class ReportResponse(BaseModel):
    batch_id: str
    domain: str
    summary: str


class FeedbackRequest(BaseModel):
    input_id: str
    analyst_override: str
    cleaned_text: str


class FeedbackResponse(BaseModel):
    input_id: str
    status: str
