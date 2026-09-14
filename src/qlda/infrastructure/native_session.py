from __future__ import annotations

"""Native V7.1 session adapter over the existing local PostgreSQL identity tables."""

import hashlib
from datetime import timezone
from typing import Any, Iterable

from qlda.domain.errors import AuthenticationError, AuthorizationError
from qlda.infrastructure.postgres import connect

_ALLOWED_ROLES = {"read", "update", "admin"}
_ALLOWED_APPROVAL_ROLES = {
    "",
    "CONTRACTOR",
    "SITE_MANAGEMENT",
    "CONSULTANT",
    "PROJECT_MANAGEMENT",
}

_IDENTITY_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS qlda_local_users (
    email TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'read',
    approval_role TEXT NOT NULL DEFAULT '',
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qlda_local_users_role ON qlda_local_users(role, active);

CREATE TABLE IF NOT EXISTS qlda_local_sessions (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL REFERENCES qlda_local_users(email) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qlda_local_sessions_email
    ON qlda_local_sessions(email, expires_at);
"""


def ensure_identity_schema() -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_IDENTITY_SCHEMA)
        conn.commit()


def _iso(value: Any) -> str:
    if value is None:
        return ""
    try:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return str(value)


def _normalize_role(value: Any) -> str:
    role = str(value or "read").strip().lower()
    if role not in _ALLOWED_ROLES:
        return "read"
    return role


def _normalize_approval_role(value: Any) -> str:
    raw = str(value or "").strip().upper()
    aliases = {
        "NONE": "",
        "CONTRACTOR": "CONTRACTOR",
        "SITE_MANAGEMENT": "SITE_MANAGEMENT",
        "TVGS": "CONSULTANT",
        "CONSULTANT": "CONSULTANT",
        "BQLDA": "PROJECT_MANAGEMENT",
        "PROJECT_MANAGEMENT": "PROJECT_MANAGEMENT",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in _ALLOWED_APPROVAL_ROLES else ""


def _legacy_approval_group(value: Any) -> str:
    return {
        "": "none",
        "CONTRACTOR": "contractor",
        "SITE_MANAGEMENT": "site_management",
        "CONSULTANT": "tvgs",
        "PROJECT_MANAGEMENT": "bqlda",
    }.get(_normalize_approval_role(value), "none")


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    role = _normalize_role(row.get("role"))
    approval = _normalize_approval_role(row.get("approval_role"))
    if role == "admin" and not approval:
        approval = "PROJECT_MANAGEMENT"
    return {
        "email": str(row.get("email") or ""),
        "name": str(row.get("name") or ""),
        "role": role,
        "approval_role": approval,
        "approval_group": _legacy_approval_group(approval),
        "active": bool(row.get("active", True)),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def require_session(token: str, roles: Iterable[str] | None = None) -> dict[str, Any]:
    ensure_identity_schema()
    raw = str(token or "").strip()
    if not raw:
        raise AuthenticationError("Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.")
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.* FROM qlda_local_sessions s
                   JOIN qlda_local_users u ON u.email=s.email
                   WHERE s.token_hash=%s AND s.expires_at>NOW() AND u.active=TRUE""",
                (token_hash,),
            )
            row = cur.fetchone()
    if not row:
        raise AuthenticationError("Phiên đăng nhập không hợp lệ hoặc đã hết hạn.")
    user = dict(row)
    if roles is not None:
        allowed = {str(role or "").strip().lower() for role in roles}
        if allowed and _normalize_role(user.get("role")) not in allowed:
            raise AuthorizationError("Tài khoản không có quyền thực hiện thao tác này.")
    return user


class NativeSessionAdapter:
    """SessionPort implementation with no V6 service/legacy-module dependency."""

    def current_user(self, token: str) -> dict[str, Any]:
        return public_user(require_session(token))
