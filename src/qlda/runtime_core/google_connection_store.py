from __future__ import annotations

"""Encrypted persistent Google OAuth connections for QLDA.

The OAuth application Client ID/Secret is managed separately by
``google_oauth_settings``.  This module stores the *user connection* returned by
Google (access/refresh token, expiry and granted scopes) so background sync can
continue after the Streamlit session or service restarts.

Nothing sensitive is committed to Git.  On the VPS the file lives under
``/opt/qlda/shared/config`` (or ``QLDA_SETTINGS_DIR``) and is mode 0600.
"""

import base64
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    explicit = str(os.environ.get("QLDA_SETTINGS_DIR", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    shared = Path(str(os.environ.get("QLDA_SHARED_DIR", "/opt/qlda/shared") or "/opt/qlda/shared")).expanduser()
    return shared / "config"


CONNECTION_FILE = _config_dir() / "google_connections.json"


def _runtime_value(name: str) -> str:
    value = str(os.environ.get(name, "") or "").strip()
    if value:
        return value
    try:
        import streamlit as st

        return str(st.secrets.get(name, "") or "").strip()
    except Exception:
        return ""


def _fernet():
    secret = (
        _runtime_value("QLDA_SETTINGS_MASTER_KEY")
        or _runtime_value("QLDA_LOCAL_UPLOAD_SECRET")
    ).strip()
    if not secret:
        return None
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encryption_available() -> bool:
    return _fernet() is not None


def _read_raw() -> dict[str, Any]:
    if not CONNECTION_FILE.exists():
        return {"version": 1, "connections": {}}
    try:
        raw = json.loads(CONNECTION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "connections": {}}
    if not isinstance(raw, dict):
        return {"version": 1, "connections": {}}
    raw.setdefault("version", 1)
    raw.setdefault("connections", {})
    if not isinstance(raw["connections"], dict):
        raw["connections"] = {}
    return raw


def _write_raw(payload: dict[str, Any]) -> Path:
    CONNECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=CONNECTION_FILE.name + ".",
        suffix=".tmp",
        dir=str(CONNECTION_FILE.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except Exception:
            pass
        os.replace(tmp_name, CONNECTION_FILE)
        try:
            os.chmod(CONNECTION_FILE, 0o600)
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass
    return CONNECTION_FILE


def save_project_connection(
    master_project_id: int,
    token_state: dict[str, Any],
    *,
    account_email: str = "",
    account_name: str = "",
    scopes: list[str] | tuple[str, ...] | None = None,
) -> Path:
    """Persist one Google connection for a master project.

    A single QLDA Gmail account can be shared into many contractor folders.  The
    project-level connection is therefore intentionally shared by all contractor
    data spaces under that project while the business data remains isolated by
    contractor/workspace IDs in PostgreSQL.
    """
    pid = int(master_project_id)
    if pid <= 0:
        raise ValueError("master_project_id không hợp lệ.")
    fernet = _fernet()
    if fernet is None:
        raise RuntimeError(
            "Không thể lưu phiên Google an toàn vì QLDA chưa có khóa mã hóa runtime. "
            "Cần giữ QLDA_LOCAL_UPLOAD_SECRET hoặc QLDA_SETTINGS_MASTER_KEY trên VPS."
        )
    state = dict(token_state or {})
    if not (state.get("refresh_token") or state.get("access_token")):
        raise ValueError("Google không trả về token có thể lưu.")
    token = fernet.encrypt(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    raw = _read_raw()
    raw["connections"][str(pid)] = {
        "master_project_id": pid,
        "account_email": str(account_email or "").strip().lower(),
        "account_name": str(account_name or "").strip(),
        "scopes": sorted({str(x).strip() for x in (scopes or []) if str(x).strip()}),
        "_encrypted_token_state": token,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "secret_storage": "fernet-sha256-derived",
    }
    return _write_raw(raw)


def load_project_connection(master_project_id: int) -> dict[str, Any]:
    pid = int(master_project_id)
    item = dict((_read_raw().get("connections") or {}).get(str(pid)) or {})
    if not item:
        return {}
    encrypted = str(item.pop("_encrypted_token_state", "") or "")
    state: dict[str, Any] = {}
    fernet = _fernet()
    if encrypted and fernet is not None:
        try:
            decoded = fernet.decrypt(encrypted.encode("ascii")).decode("utf-8")
            value = json.loads(decoded)
            if isinstance(value, dict):
                state = value
        except Exception:
            state = {}
    item["token_state"] = state
    item["connected"] = bool(state.get("refresh_token") or state.get("access_token"))
    return item


def delete_project_connection(master_project_id: int) -> None:
    raw = _read_raw()
    connections = raw.get("connections") or {}
    connections.pop(str(int(master_project_id)), None)
    raw["connections"] = connections
    _write_raw(raw)


def list_connections() -> list[dict[str, Any]]:
    raw = _read_raw()
    out: list[dict[str, Any]] = []
    for key, value in (raw.get("connections") or {}).items():
        item = dict(value or {})
        item.pop("_encrypted_token_state", None)
        item["master_project_id"] = int(item.get("master_project_id") or key or 0)
        out.append(item)
    return sorted(out, key=lambda x: int(x.get("master_project_id") or 0))
