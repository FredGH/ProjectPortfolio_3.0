from __future__ import annotations

import hashlib
import os
from typing import Annotated

import bcrypt as _bcrypt
import sqlalchemy as sa
from fastapi import APIRouter, Form, HTTPException, status
from jose import JWTError

from api.auth.jwt_handler import issue_access_token, issue_refresh_token, verify_token
from api.schemas.models import TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_engine: sa.Engine | None = None


def _get_engine() -> sa.Engine:
    global _engine
    if _engine is None:
        _engine = sa.create_engine(os.environ["DATABASE_URL"])
    return _engine


def _get_client(client_id: str) -> dict | None:
    sql = sa.text(
        """
        SELECT client_id, client_secret_hash, role, counterparty_id, legal_entity
        FROM auth.api_clients
        WHERE client_id = :cid AND is_active = TRUE
    """
    )
    with _get_engine().connect() as conn:
        row = conn.execute(sql, {"cid": client_id}).mappings().first()
    return dict(row) if row else None


def _store_refresh_token(client_id: str, token: str, expires_days: int) -> None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    sql = sa.text(
        """
        INSERT INTO auth.refresh_tokens (client_id, token_hash, expires_at)
        VALUES (:cid, :hash, NOW() + INTERVAL ':days days')
    """
    )
    with _get_engine().begin() as conn:
        conn.execute(sql, {"cid": client_id, "hash": token_hash, "days": expires_days})


def _validate_refresh_token(token: str) -> dict | None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    sql = sa.text(
        """
        SELECT rt.client_id, ac.role, ac.counterparty_id, ac.legal_entity
        FROM auth.refresh_tokens AS rt
        JOIN auth.api_clients AS ac USING (client_id)
        WHERE rt.token_hash = :hash
          AND rt.revoked = FALSE
          AND rt.expires_at > NOW()
          AND ac.is_active = TRUE
    """
    )
    with _get_engine().connect() as conn:
        row = conn.execute(sql, {"hash": token_hash}).mappings().first()
    return dict(row) if row else None


@router.post("/token", response_model=TokenResponse)
def get_token(
    client_id: Annotated[str, Form()],
    client_secret: Annotated[str, Form()],
) -> TokenResponse:
    client = _get_client(client_id)
    if client is None or not _bcrypt.checkpw(
        client_secret.encode("utf-8"), client["client_secret_hash"].encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client_id or client_secret",
        )

    access = issue_access_token(
        client_id=client_id,
        role=client["role"],
        counterparty_id=client["counterparty_id"],
        legal_entity=client["legal_entity"],
    )
    refresh = issue_refresh_token(client_id)

    try:
        from api.auth.jwt_handler import REFRESH_TOKEN_EXPIRE_DAYS

        _store_refresh_token(client_id, refresh, REFRESH_TOKEN_EXPIRE_DAYS)
    except Exception:
        pass  # Non-fatal: refresh token storage failure doesn't block login

    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    refresh_token_str: Annotated[str, Form(alias="refresh_token")],
) -> TokenResponse:
    try:
        claims = verify_token(refresh_token_str)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        ) from exc

    if claims.get("token_type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not a refresh token"
        )

    client_data = _validate_refresh_token(refresh_token_str)
    if client_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked or expired",
        )

    access = issue_access_token(
        client_id=client_data["client_id"],
        role=client_data["role"],
        counterparty_id=client_data["counterparty_id"],
        legal_entity=client_data["legal_entity"],
    )
    new_refresh = issue_refresh_token(client_data["client_id"])
    return TokenResponse(access_token=access, refresh_token=new_refresh)
