from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, status

from api.auth.dependencies import UserClaims, get_current_user

_ROLE_HIERARCHY = {
    "ADMIN": 5,
    "HEAD_OF_TRADING": 4,
    "COMPLIANCE": 3,
    "TRADER": 2,
    "CLIENT": 1,
}


def require_role(*allowed_roles: str) -> Callable:
    """Dependency factory: raises 403 if user's role is not in allowed_roles."""
    def dependency(user: UserClaims = Depends(get_current_user)) -> UserClaims:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' is not authorised for this endpoint.",
            )
        return user
    return dependency


def require_min_role(min_role: str) -> Callable:
    """Dependency factory: raises 403 if user's role level is below min_role."""
    min_level = _ROLE_HIERARCHY.get(min_role, 0)

    def dependency(user: UserClaims = Depends(get_current_user)) -> UserClaims:
        user_level = _ROLE_HIERARCHY.get(user.role, 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Minimum required role: {min_role}",
            )
        return user
    return dependency
