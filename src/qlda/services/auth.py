from __future__ import annotations

from typing import Any, Iterable

from qlda.shared.legacy import resolve


class AuthenticationError(PermissionError):
    """Raised when the existing QLDA session token is missing or invalid."""


class AuthorizationError(PermissionError):
    """Raised when a valid QLDA account does not have the required global role."""


class SessionService:
    """Reuse the existing local-VPS session/authentication model for HTTP APIs."""

    IMPLEMENTATION = "local_vps_backend_v622"

    @classmethod
    def _call(cls, name: str, *args: Any, **kwargs: Any):
        return resolve(cls.IMPLEMENTATION, name)(*args, **kwargs)

    @classmethod
    def current_user(cls, token: str) -> dict[str, Any]:
        raw = str(token or "").strip()
        if not raw:
            raise AuthenticationError("Thiếu phiên đăng nhập QLDA.")
        try:
            payload = cls._call("me", raw)
        except Exception as exc:
            raise AuthenticationError(str(exc) or "Phiên đăng nhập không hợp lệ.") from exc
        user = dict((payload or {}).get("user") or {})
        if not user or not user.get("email"):
            raise AuthenticationError("Phiên đăng nhập không hợp lệ.")
        if not bool(user.get("active", True)):
            raise AuthenticationError("Tài khoản đã bị khóa.")
        return user

    @classmethod
    def require_roles(cls, token: str, roles: Iterable[str]) -> dict[str, Any]:
        user = cls.current_user(token)
        allowed = {str(role or "").strip().lower() for role in roles}
        role = str(user.get("role") or "read").strip().lower()
        if allowed and role not in allowed:
            raise AuthorizationError("Tài khoản không có quyền thực hiện thao tác này.")
        return user
