from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from qlda.services.auth import AuthenticationError, SessionService


@dataclass(frozen=True)
class Principal:
    token: str
    user: dict[str, Any]

    @property
    def email(self) -> str:
        return str(self.user.get("email") or "")

    @property
    def role(self) -> str:
        return str(self.user.get("role") or "read").strip().lower()

    @property
    def approval_role(self) -> str:
        return str(self.user.get("approval_role") or "")


_bearer = HTTPBearer(auto_error=False)


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Thiếu Bearer session token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user = SessionService.current_user(credentials.credentials)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return Principal(token=credentials.credentials, user=user)


def require_roles(*roles: str) -> Callable[[Principal], Principal]:
    allowed = {str(role or "").strip().lower() for role in roles}

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if allowed and principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tài khoản không có quyền thực hiện thao tác này.",
            )
        return principal

    return dependency
