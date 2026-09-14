from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote


PATCH_VERSION = "V6.22 LOCAL VPS BACKEND V1"
_ALLOWED_ROLES = {"read", "update", "admin"}
_ALLOWED_APPROVAL_ROLES = {"", "CONTRACTOR", "SITE_MANAGEMENT", "CONSULTANT", "PROJECT_MANAGEMENT"}


class LocalVPSError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or default).strip()


def database_url() -> str:
    value = _env("DATABASE_URL") or _env("QLDA_DATABASE_URL") or _env("POSTGRES_URL")
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    if not value:
        raise LocalVPSError("Chưa cấu hình DATABASE_URL cho PostgreSQL local.")
    return value


def storage_root() -> Path:
    root = Path(_env("QLDA_LOCAL_STORAGE_ROOT", "/opt/qlda/data")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def trash_root() -> Path:
    root = Path(_env("QLDA_LOCAL_TRASH_ROOT", str(storage_root() / ".trash"))).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def public_base_url() -> str:
    return _env("QLDA_PUBLIC_BASE_URL").rstrip("/")


def local_secret() -> bytes:
    value = _env("QLDA_LOCAL_UPLOAD_SECRET")
    if len(value) < 32:
        raise LocalVPSError("QLDA_LOCAL_UPLOAD_SECRET phải có tối thiểu 32 ký tự.")
    return value.encode("utf-8")


def session_ttl_seconds() -> int:
    try:
        hours = int(_env("QLDA_LOCAL_SESSION_TTL_HOURS", "12"))
    except Exception:
        hours = 12
    return max(1, min(hours, 24 * 30)) * 3600


def direct_max_bytes() -> int:
    try:
        mb = int(_env("QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB", "2048"))
    except Exception:
        mb = 2048
    return max(1, min(mb, 4096)) * 1024 * 1024


def legacy_max_bytes() -> int:
    try:
        mb = int(_env("QLDA_LOCAL_LEGACY_MAX_UPLOAD_MB", "200"))
    except Exception:
        mb = 200
    return max(1, min(mb, 1024)) * 1024 * 1024


def _connect(*, autocommit: bool = False):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except Exception as exc:
        raise LocalVPSError("Thiếu psycopg để dùng PostgreSQL local.") from exc
    return psycopg.connect(database_url(), autocommit=autocommit, row_factory=dict_row)


_SCHEMA = r"""
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
CREATE INDEX IF NOT EXISTS idx_qlda_local_sessions_email ON qlda_local_sessions(email, expires_at);

CREATE TABLE IF NOT EXISTS qlda_local_files (
    id TEXT PRIMARY KEY,
    project_code TEXT NOT NULL,
    kind TEXT NOT NULL,
    subtype TEXT NOT NULL,
    record_code TEXT NOT NULL,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    size BIGINT NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    storage_path TEXT NOT NULL,
    upload_purpose TEXT NOT NULL DEFAULT '',
    uploaded_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    history BOOLEAN NOT NULL DEFAULT FALSE,
    trashed BOOLEAN NOT NULL DEFAULT FALSE,
    trashed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_qlda_local_files_record
    ON qlda_local_files(project_code, kind, subtype, record_code, trashed, history, created_at);
CREATE INDEX IF NOT EXISTS idx_qlda_local_files_sha ON qlda_local_files(sha256);
"""


def ensure_schema() -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)
        conn.commit()


def _iso(value: Any) -> str:
    if value is None:
        return ""
    try:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return str(value)


def _normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email or len(email) > 254:
        raise LocalVPSError("Email không hợp lệ.")
    return email


def _normalize_role(value: str) -> str:
    role = str(value or "read").strip().lower()
    if role not in _ALLOWED_ROLES:
        raise LocalVPSError("Quyền người dùng không hợp lệ.")
    return role


def _normalize_approval_role(value: str) -> str:
    raw = str(value or "").strip().upper()
    legacy = {
        "NONE": "",
        "CONTRACTOR": "CONTRACTOR",
        "SITE_MANAGEMENT": "SITE_MANAGEMENT",
        "TVGS": "CONSULTANT",
        "CONSULTANT": "CONSULTANT",
        "BQLDA": "PROJECT_MANAGEMENT",
        "PROJECT_MANAGEMENT": "PROJECT_MANAGEMENT",
    }
    out = legacy.get(raw, raw)
    if out not in _ALLOWED_APPROVAL_ROLES:
        raise LocalVPSError("Phân loại phê duyệt không hợp lệ.")
    return out


def _legacy_approval_group(value: str) -> str:
    return {
        "": "none",
        "CONTRACTOR": "contractor",
        "SITE_MANAGEMENT": "site_management",
        "CONSULTANT": "tvgs",
        "PROJECT_MANAGEMENT": "bqlda",
    }.get(_normalize_approval_role(value), "none")


def _validate_password(password: str) -> None:
    value = str(password or "")
    if len(value) < 8:
        raise LocalVPSError("Mật khẩu phải có ít nhất 8 ký tự.")
    if len(value) > 256:
        raise LocalVPSError("Mật khẩu quá dài.")


def _password_hash(salt_hex: str, password: str) -> str:
    raw = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        bytes.fromhex(salt_hex),
        240_000,
    )
    return raw.hex()


def _public_user(row: dict[str, Any]) -> dict[str, Any]:
    role = _normalize_role(row.get("role") or "read")
    approval = _normalize_approval_role(row.get("approval_role") or "")
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


def health() -> dict[str, Any]:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM qlda_local_users WHERE active=TRUE")
            count = int(cur.fetchone()["n"] or 0)
    root = storage_root()
    usage = shutil.disk_usage(root)
    return {
        "ok": True,
        "initialized": count > 0,
        "user_count": count,
        "root": {"id": "local-vps", "name": "QLDA VPS Local Storage", "url": ""},
        "version": PATCH_VERSION,
        "direct_upload": True,
        "max_file_bytes": direct_max_bytes(),
        "max_file_gb": round(direct_max_bytes() / 1024 / 1024 / 1024, 1),
        "disk_total": int(usage.total),
        "disk_free": int(usage.free),
    }


def bootstrap_admin(email: str, name: str, password: str, bootstrap_code: str) -> dict[str, Any]:
    ensure_schema()
    expected = _env("QLDA_LOCAL_BOOTSTRAP_CODE")
    if not expected or len(expected) < 8:
        raise LocalVPSError("Chưa cấu hình QLDA_LOCAL_BOOTSTRAP_CODE trên VPS.")
    if not hmac.compare_digest(str(bootstrap_code or ""), expected):
        raise LocalVPSError("Mã khởi tạo Admin không đúng.")
    email = _normalize_email(email)
    _validate_password(password)
    salt = secrets.token_hex(16)
    now = datetime.now(timezone.utc)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM qlda_local_users")
            if int(cur.fetchone()["n"] or 0) > 0:
                raise LocalVPSError("Hệ thống đã có tài khoản Admin; bootstrap đã bị khóa.")
            cur.execute(
                """INSERT INTO qlda_local_users
                   (email,name,role,approval_role,password_salt,password_hash,active,created_at,updated_at)
                   VALUES (%s,%s,'admin','PROJECT_MANAGEMENT',%s,%s,TRUE,%s,%s)
                   RETURNING *""",
                (email, str(name or "").strip(), salt, _password_hash(salt, password), now, now),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return {"ok": True, "user": _public_user(row)}


def login(email: str, password: str) -> dict[str, Any]:
    ensure_schema()
    email = _normalize_email(email)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM qlda_local_users WHERE email=%s AND active=TRUE", (email,))
            row = cur.fetchone()
            if not row or not hmac.compare_digest(
                str(row.get("password_hash") or ""),
                _password_hash(str(row.get("password_salt") or ""), str(password or "")),
            ):
                raise LocalVPSError("Email hoặc mật khẩu không đúng.")
            raw_token = secrets.token_urlsafe(48)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            cur.execute("DELETE FROM qlda_local_sessions WHERE expires_at <= NOW()")
            cur.execute(
                """INSERT INTO qlda_local_sessions(token_hash,email,expires_at)
                   VALUES (%s,%s,NOW() + (%s * INTERVAL '1 second'))""",
                (token_hash, email, session_ttl_seconds()),
            )
        conn.commit()
    return {"ok": True, "session_token": raw_token, "user": _public_user(dict(row))}


def _require_session(token: str, roles: set[str] | None = None) -> dict[str, Any]:
    ensure_schema()
    token = str(token or "").strip()
    if not token:
        raise LocalVPSError("Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.* FROM qlda_local_sessions s
                   JOIN qlda_local_users u ON u.email=s.email
                   WHERE s.token_hash=%s AND s.expires_at>NOW() AND u.active=TRUE""",
                (token_hash,),
            )
            row = cur.fetchone()
    if not row:
        raise LocalVPSError("Phiên đăng nhập không hợp lệ hoặc đã hết hạn.")
    user = dict(row)
    if roles is not None and _normalize_role(user.get("role") or "read") not in roles:
        raise LocalVPSError("Tài khoản không có quyền thực hiện thao tác này.")
    return user


def me(token: str) -> dict[str, Any]:
    return {"ok": True, "user": _public_user(_require_session(token))}


def root_info(token: str) -> dict[str, Any]:
    _require_session(token)
    return {"ok": True, "root": {"id": "local-vps", "name": "QLDA VPS Local Storage", "url": ""}}


def list_users(token: str) -> dict[str, Any]:
    actor = _require_session(token, {"admin"})
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM qlda_local_users ORDER BY LOWER(name), email")
            rows = [dict(x) for x in cur.fetchall()]
    return {"ok": True, "users": [_public_user(x) for x in rows], "requested_by": actor["email"]}


def approval_users(token: str) -> dict[str, Any]:
    actor = _require_session(token)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM qlda_local_users WHERE active=TRUE ORDER BY LOWER(name), email")
            rows = [dict(x) for x in cur.fetchall()]
    users = [_public_user(x) for x in rows]
    users = [u for u in users if u.get("approval_role")]
    return {"ok": True, "users": users, "requested_by": actor["email"]}


def set_user(token: str, *, email: str, name: str, role: str, password: str = "", approval_role: str = "") -> dict[str, Any]:
    _require_session(token, {"admin"})
    email = _normalize_email(email)
    role = _normalize_role(role)
    approval = _normalize_approval_role(approval_role)
    if role == "admin" and not approval:
        approval = "PROJECT_MANAGEMENT"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM qlda_local_users WHERE email=%s", (email,))
            existing = cur.fetchone()
            if existing:
                if password:
                    _validate_password(password)
                    salt = secrets.token_hex(16)
                    cur.execute(
                        """UPDATE qlda_local_users
                           SET name=%s, role=%s, approval_role=%s, active=TRUE,
                               password_salt=%s, password_hash=%s, updated_at=NOW()
                           WHERE email=%s RETURNING *""",
                        (str(name or "").strip() or existing.get("name") or "", role, approval,
                         salt, _password_hash(salt, password), email),
                    )
                else:
                    cur.execute(
                        """UPDATE qlda_local_users
                           SET name=%s, role=%s, approval_role=%s, active=TRUE, updated_at=NOW()
                           WHERE email=%s RETURNING *""",
                        (str(name or "").strip() or existing.get("name") or "", role, approval, email),
                    )
            else:
                _validate_password(password)
                salt = secrets.token_hex(16)
                cur.execute(
                    """INSERT INTO qlda_local_users
                       (email,name,role,approval_role,password_salt,password_hash,active)
                       VALUES (%s,%s,%s,%s,%s,%s,TRUE) RETURNING *""",
                    (email, str(name or "").strip(), role, approval, salt, _password_hash(salt, password)),
                )
            row = dict(cur.fetchone())
        conn.commit()
    return {"ok": True, "user": _public_user(row)}


def delete_user(token: str, email: str) -> dict[str, Any]:
    actor = _require_session(token, {"admin"})
    email = _normalize_email(email)
    if email == str(actor.get("email") or "").lower():
        raise LocalVPSError("Không thể tự xóa tài khoản Admin đang đăng nhập.")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT role FROM qlda_local_users WHERE email=%s", (email,))
            row = cur.fetchone()
            if not row:
                raise LocalVPSError("Không tìm thấy người dùng.")
            if str(row.get("role") or "") == "admin":
                cur.execute("SELECT COUNT(*) AS n FROM qlda_local_users WHERE role='admin' AND active=TRUE AND email<>%s", (email,))
                if int(cur.fetchone()["n"] or 0) < 1:
                    raise LocalVPSError("Phải còn ít nhất một Admin.")
            cur.execute("DELETE FROM qlda_local_users WHERE email=%s", (email,))
        conn.commit()
    return {"ok": True, "deleted": email}


def change_password(token: str, old_password: str, new_password: str) -> dict[str, Any]:
    actor = _require_session(token)
    _validate_password(new_password)
    email = str(actor["email"])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM qlda_local_users WHERE email=%s", (email,))
            row = cur.fetchone()
            if not row or not hmac.compare_digest(
                str(row.get("password_hash") or ""),
                _password_hash(str(row.get("password_salt") or ""), str(old_password or "")),
            ):
                raise LocalVPSError("Mật khẩu hiện tại không đúng.")
            salt = secrets.token_hex(16)
            cur.execute(
                "UPDATE qlda_local_users SET password_salt=%s,password_hash=%s,updated_at=NOW() WHERE email=%s",
                (salt, _password_hash(salt, new_password), email),
            )
        conn.commit()
    return {"ok": True, "changed": True}


def send_approval_email(token: str, **_: Any) -> dict[str, Any]:
    _require_session(token)
    return {"ok": True, "sent": False, "local_only": True}


def _safe_segment(value: str, fallback: str = "item") -> str:
    text = str(value or "").strip().replace("\\", "_").replace("/", "_")
    text = "".join(ch if (ch.isalnum() or ch in "._- ()[]#") else "_" for ch in text)
    text = " ".join(text.split()).strip(" .")
    if text in {"", ".", ".."}:
        text = fallback
    return text[:120]


def _relative_target(project_code: str, kind: str, subtype: str, record_code: str, file_id: str, name: str) -> Path:
    return Path("projects") / _safe_segment(project_code, "DU_AN") / _safe_segment(kind, "file") / _safe_segment(subtype, "Khac") / _safe_segment(record_code, "Chung") / f"{file_id}__{_safe_segment(name, 'attachment')}"


def _absolute_from_relative(value: str) -> Path:
    root = storage_root()
    path = (root / str(value or "")).resolve()
    try:
        path.relative_to(root)
    except Exception as exc:
        raise LocalVPSError("Đường dẫn file không hợp lệ.") from exc
    return path


def _file_url(file_id: str, *, download: bool = False, ttl: int = 3600) -> str:
    base = public_base_url()
    if not base:
        return ""
    exp = int(time.time()) + max(60, min(int(ttl), 24 * 3600))
    mode = "download" if download else "inline"
    message = f"file|{file_id}|{mode}|{exp}".encode("utf-8")
    sig = hmac.new(local_secret(), message, hashlib.sha256).hexdigest()
    return f"{base}/qlda-files/file/{quote(file_id)}?exp={exp}&mode={mode}&sig={sig}"


def verify_file_signature(file_id: str, exp: int, mode: str, sig: str) -> bool:
    try:
        exp_i = int(exp)
    except Exception:
        return False
    if exp_i < int(time.time()) - 5:
        return False
    mode = "download" if str(mode) == "download" else "inline"
    expected = hmac.new(local_secret(), f"file|{file_id}|{mode}|{exp_i}".encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(str(sig or ""), expected)


def _file_public(row: dict[str, Any]) -> dict[str, Any]:
    file_id = str(row.get("id") or "")
    return {
        "id": file_id,
        "name": str(row.get("name") or "attachment"),
        "url": _file_url(file_id, download=False),
        "webViewLink": _file_url(file_id, download=False),
        "download_url": _file_url(file_id, download=True),
        "mime_type": str(row.get("mime_type") or "application/octet-stream"),
        "size": int(row.get("size") or 0),
        "modified_time": _iso(row.get("modified_at")),
        "folder_url": "",
        "history": bool(row.get("history", False)),
    }


def _register_path(*, actor_email: str, project_code: str, kind: str, subtype: str, record_code: str,
                   name: str, mime_type: str, path: Path, sha256_hex: str, size: int, upload_purpose: str = "") -> dict[str, Any]:
    file_id = path.name.split("__", 1)[0]
    rel = str(path.resolve().relative_to(storage_root())).replace(os.sep, "/")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE qlda_local_files SET history=TRUE, modified_at=NOW()
                   WHERE project_code=%s AND kind=%s AND subtype=%s AND record_code=%s
                     AND LOWER(name)=LOWER(%s) AND trashed=FALSE AND history=FALSE""",
                (project_code, kind, subtype, record_code, name),
            )
            cur.execute(
                """INSERT INTO qlda_local_files
                   (id,project_code,kind,subtype,record_code,name,mime_type,size,sha256,storage_path,upload_purpose,uploaded_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (file_id, project_code, kind, subtype, record_code, name,
                 mime_type or "application/octet-stream", int(size), sha256_hex, rel,
                 str(upload_purpose or ""), actor_email),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return _file_public(row)


def save_bytes(token: str, *, project_code: str, kind: str, subtype: str, record_code: str,
               name: str, content: bytes, mime_type: str = "", upload_purpose: str = "") -> dict[str, Any]:
    actor = _require_session(token, {"update", "admin"})
    data = bytes(content)
    if len(data) > legacy_max_bytes():
        raise LocalVPSError(
            f"File {name} ({len(data)/1024/1024:.1f} MB) vượt giới hạn upload trong app "
            f"{legacy_max_bytes()/1024/1024:.0f} MB. Hãy dùng nút tải file lớn trực tiếp lên VPS."
        )
    file_id = uuid.uuid4().hex
    rel = _relative_target(project_code, kind, subtype, record_code, file_id, name)
    target = _absolute_from_relative(str(rel))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = storage_root() / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{file_id}.part"
    digest = hashlib.sha256()
    with tmp.open("wb") as fh:
        fh.write(data)
        digest.update(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o640)
    os.replace(tmp, target)
    return _register_path(
        actor_email=str(actor["email"]), project_code=_safe_segment(project_code, "DU_AN"),
        kind=_safe_segment(kind, "file"), subtype=_safe_segment(subtype, "Khac"),
        record_code=_safe_segment(record_code, "Chung"), name=_safe_segment(name, "attachment"),
        mime_type=mime_type or mimetypes.guess_type(str(name))[0] or "application/octet-stream",
        path=target, sha256_hex=digest.hexdigest(), size=len(data), upload_purpose=upload_purpose,
    )


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    value = str(text or "")
    value += "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value.encode("ascii"))


def make_upload_ticket(token: str, *, project_code: str, kind: str, subtype: str, record_code: str,
                       upload_purpose: str = "", max_bytes: int | None = None) -> dict[str, Any]:
    actor = _require_session(token, {"update", "admin"})
    limit = min(int(max_bytes or direct_max_bytes()), direct_max_bytes())
    payload = {
        "v": 1,
        "email": str(actor["email"]),
        "role": str(actor["role"]),
        "project_code": _safe_segment(project_code, "DU_AN"),
        "kind": _safe_segment(kind, "file"),
        "subtype": _safe_segment(subtype, "Khac"),
        "record_code": _safe_segment(record_code, "Chung"),
        "upload_purpose": str(upload_purpose or "")[:200],
        "max_bytes": int(limit),
        "exp": int(time.time()) + 6 * 3600,
        "nonce": secrets.token_hex(12),
    }
    body = _b64url_encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(local_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    ticket = body + "." + sig
    base = public_base_url()
    url = f"{base}/qlda-files/upload?ticket={quote(ticket)}" if base else ""
    return {
        "ticket": ticket,
        "url": url,
        "folder_url": "",
        "max_bytes": int(limit),
        "max_gb": round(limit / 1024 / 1024 / 1024, 1),
        "expires_seconds": 6 * 3600,
        "ticket_version": 1,
    }


def verify_upload_ticket(ticket: str) -> dict[str, Any]:
    try:
        body, sig = str(ticket or "").rsplit(".", 1)
        expected = hmac.new(local_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise LocalVPSError("Ticket upload không hợp lệ.")
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except LocalVPSError:
        raise
    except Exception as exc:
        raise LocalVPSError("Ticket upload không hợp lệ.") from exc
    if int(payload.get("exp") or 0) < int(time.time()):
        raise LocalVPSError("Ticket upload đã hết hạn.")
    if int(payload.get("max_bytes") or 0) <= 0:
        raise LocalVPSError("Ticket upload thiếu giới hạn dung lượng.")
    return dict(payload)


def save_stream_from_ticket(ticket: str, *, name: str, mime_type: str, stream: BinaryIO, content_length: int) -> dict[str, Any]:
    meta = verify_upload_ticket(ticket)
    length = int(content_length)
    max_bytes = min(int(meta.get("max_bytes") or 0), direct_max_bytes())
    if length <= 0:
        raise LocalVPSError("File rỗng hoặc thiếu Content-Length.")
    if length > max_bytes:
        raise LocalVPSError(f"File vượt giới hạn {max_bytes/1024/1024:.0f} MB.")
    file_id = uuid.uuid4().hex
    safe_name = _safe_segment(name, "attachment")
    rel = _relative_target(meta["project_code"], meta["kind"], meta["subtype"], meta["record_code"], file_id, safe_name)
    target = _absolute_from_relative(str(rel))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = storage_root() / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{file_id}.part"
    digest = hashlib.sha256()
    remaining = length
    written = 0
    try:
        with tmp.open("wb") as fh:
            while remaining > 0:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                remaining -= len(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        if written != length:
            raise LocalVPSError(f"Upload chưa đủ dữ liệu: nhận {written} / {length} byte.")
        os.chmod(tmp, 0o640)
        os.replace(tmp, target)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return _register_path(
        actor_email=str(meta.get("email") or ""), project_code=str(meta["project_code"]),
        kind=str(meta["kind"]), subtype=str(meta["subtype"]), record_code=str(meta["record_code"]),
        name=safe_name, mime_type=mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
        path=target, sha256_hex=digest.hexdigest(), size=written,
        upload_purpose=str(meta.get("upload_purpose") or ""),
    )


def list_record_files(token: str, *, project_code: str, kind: str, subtype: str, record_code: str,
                      include_history: bool = False) -> dict[str, Any]:
    _require_session(token)
    params = (_safe_segment(project_code, "DU_AN"), _safe_segment(kind, "file"), _safe_segment(subtype, "Khac"), _safe_segment(record_code, "Chung"))
    with _connect() as conn:
        with conn.cursor() as cur:
            if include_history:
                cur.execute(
                    """SELECT * FROM qlda_local_files
                       WHERE project_code=%s AND kind=%s AND subtype=%s AND record_code=%s AND trashed=FALSE
                       ORDER BY history ASC, created_at DESC""",
                    params,
                )
            else:
                cur.execute(
                    """SELECT * FROM qlda_local_files
                       WHERE project_code=%s AND kind=%s AND subtype=%s AND record_code=%s
                         AND trashed=FALSE AND history=FALSE ORDER BY created_at DESC""",
                    params,
                )
            rows = [dict(x) for x in cur.fetchall()]
    return {"ok": True, "files": [_file_public(x) for x in rows], "folder": {"id": "local", "name": params[-1], "url": ""}}


def record_file_counts(token: str, *, project_code: str, kind: str, subtype: str, record_codes: list[str]) -> dict[str, Any]:
    _require_session(token)
    codes = [_safe_segment(x, "Chung") for x in record_codes if str(x or "").strip()]
    if not codes:
        return {"ok": True, "counts": {}}
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT record_code, COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes
                   FROM qlda_local_files
                   WHERE project_code=%s AND kind=%s AND subtype=%s AND trashed=FALSE AND history=FALSE
                     AND record_code = ANY(%s)
                   GROUP BY record_code""",
                (_safe_segment(project_code, "DU_AN"), _safe_segment(kind, "file"), _safe_segment(subtype, "Khac"), codes),
            )
            rows = [dict(x) for x in cur.fetchall()]
    result = {code: {"count": 0, "size": 0} for code in codes}
    for row in rows:
        result[str(row["record_code"])] = {"count": int(row["n"] or 0), "size": int(row["bytes"] or 0)}
    return {"ok": True, "counts": result}


def get_file_row(file_id: str, *, include_trashed: bool = False) -> dict[str, Any]:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            if include_trashed:
                cur.execute("SELECT * FROM qlda_local_files WHERE id=%s", (str(file_id),))
            else:
                cur.execute("SELECT * FROM qlda_local_files WHERE id=%s AND trashed=FALSE", (str(file_id),))
            row = cur.fetchone()
    if not row:
        raise LocalVPSError("Không tìm thấy file trên VPS.")
    return dict(row)


def file_info(token: str, file_id: str) -> dict[str, Any]:
    _require_session(token)
    return {"ok": True, "file": _file_public(get_file_row(file_id))}


def download_bytes(token: str, file_id: str) -> tuple[str, str, bytes]:
    _require_session(token)
    row = get_file_row(file_id)
    path = _absolute_from_relative(str(row.get("storage_path") or ""))
    if not path.exists():
        raise LocalVPSError("File vật lý không còn trên VPS.")
    return str(row.get("name") or "attachment"), str(row.get("mime_type") or "application/octet-stream"), path.read_bytes()


def local_file_path(file_id: str) -> tuple[dict[str, Any], Path]:
    row = get_file_row(file_id)
    path = _absolute_from_relative(str(row.get("storage_path") or ""))
    if not path.exists():
        raise LocalVPSError("File vật lý không còn trên VPS.")
    return row, path


def trash_file(token: str, file_id: str) -> dict[str, Any]:
    _require_session(token, {"admin"})
    row, path = local_file_path(file_id)
    target_root = trash_root()
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / f"{file_id}__{_safe_segment(str(row.get('name') or 'attachment'))}"
    if path.exists():
        os.replace(path, target)
    rel = str(target.resolve().relative_to(storage_root())).replace(os.sep, "/")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE qlda_local_files SET trashed=TRUE,trashed_at=NOW(),modified_at=NOW(),storage_path=%s WHERE id=%s",
                (rel, str(file_id)),
            )
        conn.commit()
    return {"ok": True, "trashed": str(file_id)}


def dispatch(action: str, payload: dict[str, Any] | None = None, session_token: str = "") -> dict[str, Any]:
    action = str(action or "").strip()
    p = dict(payload or {})
    if action == "health":
        return health()
    if action == "bootstrap":
        return bootstrap_admin(p.get("email", ""), p.get("name", ""), p.get("password", ""), p.get("bootstrap_code", ""))
    if action == "login":
        return login(p.get("email", ""), p.get("password", ""))
    if action == "me":
        return me(session_token)
    if action == "root_info":
        return root_info(session_token)
    if action == "list_users":
        return list_users(session_token)
    if action == "approval_users":
        return approval_users(session_token)
    if action == "set_user":
        return set_user(session_token, email=p.get("email", ""), name=p.get("name", ""), role=p.get("role", "read"), password=p.get("password", ""), approval_role=p.get("approval_role") or p.get("approval_group") or "")
    if action == "delete_user":
        return delete_user(session_token, p.get("email", ""))
    if action == "change_password":
        return change_password(session_token, p.get("old_password", ""), p.get("new_password", ""))
    if action == "send_approval_email":
        return send_approval_email(session_token, **p)
    if action == "create_upload_ticket":
        return {"ok": True, "upload": make_upload_ticket(
            session_token, project_code=p.get("project_code", ""), kind=p.get("kind", ""), subtype=p.get("subtype", ""),
            record_code=p.get("record_code", ""), upload_purpose=p.get("upload_purpose", ""), max_bytes=p.get("max_bytes") or direct_max_bytes())}
    if action == "list_record_files":
        return list_record_files(session_token, project_code=p.get("project_code", ""), kind=p.get("kind", ""), subtype=p.get("subtype", ""), record_code=p.get("record_code", ""), include_history=bool(p.get("include_history")))
    if action == "record_file_counts":
        return record_file_counts(session_token, project_code=p.get("project_code", ""), kind=p.get("kind", ""), subtype=p.get("subtype", ""), record_codes=list(p.get("record_codes") or []))
    if action == "file_info":
        return file_info(session_token, p.get("file_id", ""))
    if action == "trash_file":
        return trash_file(session_token, p.get("file_id", ""))
    if action == "upload_legacy":
        try:
            content = base64.b64decode(p.get("file_base64") or "")
        except Exception as exc:
            raise LocalVPSError("Không giải mã được file upload.") from exc
        item = save_bytes(session_token, project_code=p.get("project_code", ""), kind=p.get("kind", ""), subtype=p.get("subtype", ""), record_code=p.get("record_code", ""), name=p.get("file_name", "attachment"), content=content, mime_type=p.get("mime_type", ""), upload_purpose=p.get("upload_purpose", ""))
        return {"ok": True, "file": item}
    if action == "download_legacy":
        name, mime, content = download_bytes(session_token, p.get("file_id", ""))
        return {"ok": True, "file": {"name": name, "mime_type": mime, "file_base64": base64.b64encode(content).decode("ascii")}}
    raise LocalVPSError(f"Action local không được hỗ trợ: {action}")
