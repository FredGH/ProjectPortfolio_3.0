from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.auth.dependencies import UserClaims
from api.auth.rbac import require_min_role
from api.schemas.models import ObsWarning
from api.services.tca_service import TCAService

router = APIRouter(prefix="/reports", tags=["reports"])
_svc = TCAService()


@router.get("/warning", response_model=list[ObsWarning])
def get_warnings(
    user: Annotated[UserClaims, Depends(require_min_role("TRADER"))],
    limit: Annotated[int, Query(le=500)] = 100,
) -> list[ObsWarning]:
    rows = _svc.get_warnings(limit=limit)
    return [ObsWarning(**r) for r in rows]
