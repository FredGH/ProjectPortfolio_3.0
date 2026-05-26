from __future__ import annotations

import os
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.auth.dependencies import UserClaims
from api.auth.rbac import require_role

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
_ADMIN_ONLY = require_role("ADMIN")

_AIRFLOW_BASE = os.environ.get("AIRFLOW_BASE_URL", "http://airflow-webserver:8080")
_AIRFLOW_USER = os.environ.get("AIRFLOW_ADMIN_USER", "admin")
_AIRFLOW_PASS = os.environ.get("AIRFLOW_ADMIN_PASSWORD", "admin")


class PipelineRunRequest(BaseModel):
    dag_id: str = Field(example="tca_ingestion")
    conf: dict = Field(default={}, example={"param1": "value1"})


class PipelineRunResponse(BaseModel):
    dag_run_id: str
    dag_id: str
    state: str


@router.post("/run", response_model=PipelineRunResponse)
async def trigger_dag(
    body: PipelineRunRequest,
    user: Annotated[UserClaims, Depends(_ADMIN_ONLY)],
) -> PipelineRunResponse:
    url = f"{_AIRFLOW_BASE}/api/v1/dags/{body.dag_id}/dagRuns"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={"conf": body.conf},
            auth=(_AIRFLOW_USER, _AIRFLOW_PASS),
            timeout=10.0,
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Airflow returned {resp.status_code}: {resp.text}",
        )
    data = resp.json()
    return PipelineRunResponse(
        dag_run_id=data.get("dag_run_id", ""),
        dag_id=data.get("dag_id", body.dag_id),
        state=data.get("state", "queued"),
    )
