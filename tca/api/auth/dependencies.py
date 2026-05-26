from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from api.auth.jwt_handler import verify_token

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class UserClaims:
    client_id: str
    role: str
    counterparty_id: str | None
    legal_entity: str | None


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UserClaims:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        claims = verify_token(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if claims.get("token_type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not an access token"
        )

    return UserClaims(
        client_id=claims["sub"],
        role=claims.get("role", ""),
        counterparty_id=claims.get("counterparty_id"),
        legal_entity=claims.get("legal_entity"),
    )
