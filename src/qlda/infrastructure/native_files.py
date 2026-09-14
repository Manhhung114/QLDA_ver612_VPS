from __future__ import annotations

"""Native V7.1 local-file adapter using existing PostgreSQL metadata + VPS SSD layout.

The storage schema, ticket signature and file paths stay wire-compatible with the
V6 local upload service, but application use cases no longer call a legacy
service/module to work with files.
"""

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import time
import uuid
from datetime import timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from qlda.infrastructure.native_session import ensure_identity_schema, require_session
from qlda.infrastructure.postgres import connect

_FILE_SCHEMA = r"""
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


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or default).strip()


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
        raise RuntimeError("QLDA_LOCAL_UPLOAD_SECRET phải có tối thiểu 32 ký tự.")
    return value.encode("utf-8")


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


def ensure_file_schema() -> None:
    ensure_identity_schema()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_FILE_SCHEMA)
        conn.commit()


def _iso(value: Any) -> str:
    if value is None:
        return ""
    try:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return str(value)


def _safe_segment(value: str, fallback: str = "item") -> str:
    text = str(value or "").strip().replace("\\", "_").replace("/", "_")
    text = "".join(ch if (ch.isalnum() or ch in "._- ()[]#") else "_" for ch in text)
    text = " ".join(text.split()).strip(" .")
    if text in {"", ".", ".."}:
        text = fallback
    return text[:120]


def _relative_target(
    project_code: str,
    kind: str,
    subtype: str,
    record_code: str,
    file_id: str,
    name: str,
) -> Path:
    return (
        Path("projects")
        / _safe_segment(project_code, "DU_AN")
        / _safe_segment(kind, "file")
        / _safe_segment(subtype, "Khac")
        / _safe_segment(record_code, "Chung")
        / f"{file_id}__{_safe_segment(name, 'attachment')}"
    )


def _absolute_from_relative(value: str) -> Path:
    root = storage_root()
    path = (root / str(value or "")).resolve()
    try:
        path.relative_to(root)
    except Exception as exc:
        raise RuntimeError("Đường dẫn file không hợp lệ.") from exc
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


def file_public(row: dict[str, Any]) -> dict[str, Any]:
    file_id = str(row.get("id") or "")
    return {
        "id": file_id,
        "project_code": str(row.get("project_code") or ""),
        "kind": str(row.get("kind") or ""),
        "subtype": str(row.get("subtype") or ""),
        "record_code": str(row.get("record_code") or ""),
        "name": str(row.get("name") or "attachment"),
        "url": _file_url(file_id, download=False),
        "webViewLink": _file_url(file_id, download=False),
        "download_url": _file_url(file_id, download=True),
        "mime_type": str(row.get("mime_type") or "application/octet-stream"),
        "size": int(row.get("size") or 0),
        "sha256": str(row.get("sha256") or ""),
        "upload_purpose": str(row.get("upload_purpose") or ""),
        "uploaded_by": str(row.get("uploaded_by") or ""),
        "created_at": _iso(row.get("created_at")),
        "modified_time": _iso(row.get("modified_at")),
        "folder_url": "",
        "history": bool(row.get("history", False)),
    }


def get_file_row(file_id: str, *, include_trashed: bool = False) -> dict[str, Any]:
    ensure_file_schema()
    with connect() as conn:
        with conn.cursor() as cur:
            if include_trashed:
                cur.execute("SELECT * FROM qlda_local_files WHERE id=%s", (str(file_id),))
            else:
                cur.execute(
                    "SELECT * FROM qlda_local_files WHERE id=%s AND trashed=FALSE",
                    (str(file_id),),
                )
            row = cur.fetchone()
    if not row:
        raise FileNotFoundError("Không tìm thấy file trên VPS.")
    return dict(row)


def _register_path(
    *,
    actor_email: str,
    project_code: str,
    kind: str,
    subtype: str,
    record_code: str,
    name: str,
    mime_type: str,
    path: Path,
    sha256_hex: str,
    size: int,
    upload_purpose: str = "",
) -> dict[str, Any]:
    ensure_file_schema()
    file_id = path.name.split("__", 1)[0]
    rel = str(path.resolve().relative_to(storage_root())).replace(os.sep, "/")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE qlda_local_files SET history=TRUE, modified_at=NOW()
                   WHERE project_code=%s AND kind=%s AND subtype=%s AND record_code=%s
                     AND LOWER(name)=LOWER(%s) AND trashed=FALSE AND history=FALSE""",
                (project_code, kind, subtype, record_code, name),
            )
            cur.execute(
                """INSERT INTO qlda_local_files
                   (id,project_code,kind,subtype,record_code,name,mime_type,size,sha256,
                    storage_path,upload_purpose,uploaded_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    file_id,
                    project_code,
                    kind,
                    subtype,
                    record_code,
                    name,
                    mime_type or "application/octet-stream",
                    int(size),
                    sha256_hex,
                    rel,
                    str(upload_purpose or ""),
                    actor_email,
                ),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return file_public(row)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class NativeFileAdapter:
    """FilePort implementation independent from qlda.services and V6 modules."""

    def local_path(self, file_id: str) -> tuple[dict[str, Any], Path]:
        row = get_file_row(file_id)
        path = _absolute_from_relative(str(row.get("storage_path") or ""))
        if not path.exists():
            raise FileNotFoundError("File vật lý không còn trên VPS.")
        return row, path

    def info(self, token: str, file_id: str) -> dict[str, Any]:
        require_session(token)
        return {"ok": True, "file": file_public(get_file_row(file_id))}

    def list_record_files(self, token: str, **kwargs: Any) -> dict[str, Any]:
        require_session(token)
        project_code = _safe_segment(str(kwargs.get("project_code") or ""), "DU_AN")
        kind = _safe_segment(str(kwargs.get("kind") or ""), "file")
        subtype = _safe_segment(str(kwargs.get("subtype") or ""), "Khac")
        record_code = _safe_segment(str(kwargs.get("record_code") or ""), "Chung")
        include_history = bool(kwargs.get("include_history", False))
        ensure_file_schema()
        with connect() as conn:
            with conn.cursor() as cur:
                params = (project_code, kind, subtype, record_code)
                if include_history:
                    cur.execute(
                        """SELECT * FROM qlda_local_files
                           WHERE project_code=%s AND kind=%s AND subtype=%s AND record_code=%s
                             AND trashed=FALSE
                           ORDER BY history ASC, created_at DESC""",
                        params,
                    )
                else:
                    cur.execute(
                        """SELECT * FROM qlda_local_files
                           WHERE project_code=%s AND kind=%s AND subtype=%s AND record_code=%s
                             AND trashed=FALSE AND history=FALSE
                           ORDER BY created_at DESC""",
                        params,
                    )
                rows = [dict(row) for row in cur.fetchall()]
        return {
            "ok": True,
            "files": [file_public(row) for row in rows],
            "folder": {"id": "local", "name": record_code, "url": ""},
        }

    def record_file_counts(self, token: str, **kwargs: Any) -> dict[str, Any]:
        require_session(token)
        project_code = _safe_segment(str(kwargs.get("project_code") or ""), "DU_AN")
        kind = _safe_segment(str(kwargs.get("kind") or ""), "file")
        subtype = _safe_segment(str(kwargs.get("subtype") or ""), "Khac")
        codes = [
            _safe_segment(str(value), "Chung")
            for value in list(kwargs.get("record_codes") or [])
            if str(value or "").strip()
        ]
        if not codes:
            return {"ok": True, "counts": {}}
        ensure_file_schema()
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT record_code, COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes
                       FROM qlda_local_files
                       WHERE project_code=%s AND kind=%s AND subtype=%s
                         AND trashed=FALSE AND history=FALSE AND record_code = ANY(%s)
                       GROUP BY record_code""",
                    (project_code, kind, subtype, codes),
                )
                rows = [dict(row) for row in cur.fetchall()]
        result = {code: {"count": 0, "size": 0} for code in codes}
        for row in rows:
            result[str(row["record_code"])] = {
                "count": int(row["n"] or 0),
                "size": int(row["bytes"] or 0),
            }
        return {"ok": True, "counts": result}

    def save_bytes(self, token: str, **kwargs: Any) -> dict[str, Any]:
        actor = require_session(token, {"update", "admin"})
        name = str(kwargs.get("name") or "attachment")
        content = bytes(kwargs.get("content") or b"")
        if len(content) > legacy_max_bytes():
            raise ValueError(
                f"File {name} ({len(content)/1024/1024:.1f} MB) vượt giới hạn upload trong app "
                f"{legacy_max_bytes()/1024/1024:.0f} MB. Hãy dùng nút tải file lớn trực tiếp lên VPS."
            )
        file_id = uuid.uuid4().hex
        project_code = _safe_segment(str(kwargs.get("project_code") or ""), "DU_AN")
        kind = _safe_segment(str(kwargs.get("kind") or ""), "file")
        subtype = _safe_segment(str(kwargs.get("subtype") or ""), "Khac")
        record_code = _safe_segment(str(kwargs.get("record_code") or ""), "Chung")
        safe_name = _safe_segment(name, "attachment")
        target = _absolute_from_relative(
            str(_relative_target(project_code, kind, subtype, record_code, file_id, safe_name))
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = storage_root() / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"{file_id}.part"
        digest = hashlib.sha256()
        try:
            with tmp.open("wb") as fh:
                fh.write(content)
                digest.update(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o640)
            os.replace(tmp, target)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        return _register_path(
            actor_email=str(actor.get("email") or ""),
            project_code=project_code,
            kind=kind,
            subtype=subtype,
            record_code=record_code,
            name=safe_name,
            mime_type=(
                str(kwargs.get("mime_type") or "")
                or mimetypes.guess_type(name)[0]
                or "application/octet-stream"
            ),
            path=target,
            sha256_hex=digest.hexdigest(),
            size=len(content),
            upload_purpose=str(kwargs.get("upload_purpose") or ""),
        )

    def make_upload_ticket(self, token: str, **kwargs: Any) -> dict[str, Any]:
        actor = require_session(token, {"update", "admin"})
        limit = min(int(kwargs.get("max_bytes") or direct_max_bytes()), direct_max_bytes())
        payload = {
            "v": 1,
            "email": str(actor.get("email") or ""),
            "role": str(actor.get("role") or ""),
            "project_code": _safe_segment(str(kwargs.get("project_code") or ""), "DU_AN"),
            "kind": _safe_segment(str(kwargs.get("kind") or ""), "file"),
            "subtype": _safe_segment(str(kwargs.get("subtype") or ""), "Khac"),
            "record_code": _safe_segment(str(kwargs.get("record_code") or ""), "Chung"),
            "upload_purpose": str(kwargs.get("upload_purpose") or "")[:200],
            "max_bytes": int(limit),
            "exp": int(time.time()) + 6 * 3600,
            "nonce": secrets.token_hex(12),
        }
        body = _b64url_encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        sig = hmac.new(local_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
        ticket = body + "." + sig
        base = public_base_url()
        return {
            "ticket": ticket,
            "url": f"{base}/qlda-files/upload?ticket={quote(ticket)}" if base else "",
            "folder_url": "",
            "max_bytes": int(limit),
            "max_gb": round(limit / 1024 / 1024 / 1024, 1),
            "expires_seconds": 6 * 3600,
            "ticket_version": 1,
        }

    def trash(self, token: str, file_id: str) -> dict[str, Any]:
        require_session(token, {"admin"})
        row, path = self.local_path(file_id)
        target_root = trash_root()
        target_root.mkdir(parents=True, exist_ok=True)
        target = target_root / f"{file_id}__{_safe_segment(str(row.get('name') or 'attachment'))}"
        if path.exists():
            os.replace(path, target)
        rel = str(target.resolve().relative_to(storage_root())).replace(os.sep, "/")
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE qlda_local_files
                       SET trashed=TRUE,trashed_at=NOW(),modified_at=NOW(),storage_path=%s
                       WHERE id=%s""",
                    (rel, str(file_id)),
                )
            conn.commit()
        return {"ok": True, "trashed": str(file_id)}
