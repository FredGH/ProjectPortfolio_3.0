from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from api.auth.dependencies import UserClaims
from api.auth.rbac import require_role
from api.schemas.models import MifidRow
from api.services.tca_service import TCAService

router = APIRouter(prefix="/mifid", tags=["mifid"])
_svc = TCAService()

_COMPLIANCE_ONLY = require_role("COMPLIANCE", "ADMIN")


@router.get("/export", response_model=list[MifidRow])
def mifid_export(
    user: Annotated[UserClaims, Depends(_COMPLIANCE_ONLY)],
    trade_date: Annotated[str, Query()] = "2025-01-15",
) -> list[MifidRow]:
    rows = _svc.get_mifid_export(trade_date, user)
    return [MifidRow(**r) for r in rows]


@router.get("/export/csv")
def mifid_export_csv(
    user: Annotated[UserClaims, Depends(_COMPLIANCE_ONLY)],
    trade_date: Annotated[str, Query()] = "2025-01-15",
) -> StreamingResponse:
    import csv
    import io

    rows = _svc.get_mifid_export(trade_date, user)
    if not rows:
        return StreamingResponse(io.StringIO(""), media_type="text/csv")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=mifid_{trade_date}.csv"},
    )
