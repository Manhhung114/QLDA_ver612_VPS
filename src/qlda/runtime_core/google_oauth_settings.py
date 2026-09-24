from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DEFAULT_REDIRECT_URI = "https://qldaxd.id.vn"


def _config_dir() -> Path:
    explicit = str(os.environ.get("QLDA_SETTINGS_DIR", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    shared = Path(str(os.environ.get("QLDA_SHARED_DIR", "/opt/qlda/shared") or "/opt/qlda/shared")).expanduser()
    return shared / "config"


CONFIG_FILE = _config_dir() / "google_oauth.json"


def _runtime_value(name: str, default: str = "") -> str:
    value = str(os.environ.get(name, "") or "").strip()
    if value:
        return value
    try:
        import streamlit as st

        value = str(st.secrets.get(name, "") or "").strip()
    except Exception:
        value = ""
    return value or default


def _master_secret() -> str:
    return (
        _runtime_value("QLDA_SETTINGS_MASTER_KEY", "")
        or _runtime_value("QLDA_LOCAL_UPLOAD_SECRET", "")
    ).strip()


def encryption_available() -> bool:
    if not _master_secret():
        return False
    try:
        from cryptography.fernet import Fernet  # noqa: F401
        return True
    except Exception:
        return False


def _fernet():
    secret = _master_secret()
    if not secret:
        return None
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _read_saved() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    token = str(raw.get("_encrypted_client_secret") or "").strip()
    secret = ""
    if token:
        fernet = _fernet()
        if fernet is not None:
            try:
                secret = fernet.decrypt(token.encode("ascii")).decode("utf-8")
            except Exception:
                secret = ""
    return {
        "client_id": str(raw.get("client_id") or "").strip(),
        "client_secret": secret,
        "redirect_uri": str(raw.get("redirect_uri") or "").strip(),
        "updated_at": str(raw.get("updated_at") or "").strip(),
    }


def get_google_oauth_settings() -> dict[str, Any]:
    """Return effective OAuth config, preferring encrypted Admin-managed values."""
    saved = _read_saved()
    if saved.get("client_id") and saved.get("client_secret"):
        redirect = str(saved.get("redirect_uri") or "").strip() or DEFAULT_REDIRECT_URI
        return {
            **saved,
            "redirect_uri": redirect,
            "source": "App / Admin",
            "managed": True,
        }

    client_id = _runtime_value("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = _runtime_value("GOOGLE_OAUTH_CLIENT_SECRET", "")
    redirect_uri = (
        _runtime_value("GOOGLE_OAUTH_REDIRECT_URI", "")
        or _runtime_value("QLDA_PUBLIC_BASE_URL", "")
        or DEFAULT_REDIRECT_URI
    ).rstrip("/")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "updated_at": "",
        "source": "qlda.env / Secrets" if (client_id or client_secret) else "Chưa cấu hình",
        "managed": False,
    }


def validate_google_oauth(client_id: str, client_secret: str, redirect_uri: str) -> tuple[bool, str]:
    cid = str(client_id or "").strip()
    secret = str(client_secret or "").strip()
    redirect = str(redirect_uri or "").strip().rstrip("/")
    if not cid:
        return False, "Client ID không được để trống."
    if not secret:
        return False, "Client Secret không được để trống."
    if not redirect:
        return False, "Redirect URI không được để trống."
    try:
        parsed = urlsplit(redirect)
    except Exception:
        return False, "Redirect URI không hợp lệ."
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        return False, "Redirect URI phải là URL đầy đủ, ví dụ https://qldaxd.id.vn."
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        return False, "Redirect URI production phải dùng HTTPS."
    if parsed.query or parsed.fragment:
        return False, "Redirect URI không nên chứa query hoặc fragment."
    if not cid.endswith(".apps.googleusercontent.com"):
        return True, "Cấu hình hợp lệ. Lưu ý Client ID thường kết thúc bằng .apps.googleusercontent.com."
    return True, "Cấu hình Google OAuth hợp lệ."


def _atomic_write(payload: dict[str, Any]) -> Path:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=CONFIG_FILE.name + ".", suffix=".tmp", dir=str(CONFIG_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_name, 0o600)
        except Exception:
            pass
        os.replace(temp_name, CONFIG_FILE)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        except Exception:
            pass
    return CONFIG_FILE


def save_google_oauth_settings(client_id: str, client_secret: str, redirect_uri: str) -> Path:
    cid = str(client_id or "").strip()
    secret = str(client_secret or "").strip()
    redirect = str(redirect_uri or "").strip().rstrip("/")
    ok, message = validate_google_oauth(cid, secret, redirect)
    if not ok:
        raise ValueError(message)
    fernet = _fernet()
    if fernet is None:
        raise RuntimeError(
            "Chưa có khóa mã hóa runtime. QLDA cần QLDA_LOCAL_UPLOAD_SECRET hoặc "
            "QLDA_SETTINGS_MASTER_KEY để lưu Google Client Secret an toàn."
        )
    payload = {
        "client_id": cid,
        "redirect_uri": redirect,
        "_encrypted_client_secret": fernet.encrypt(secret.encode("utf-8")).decode("ascii"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "secret_storage": "fernet-sha256-derived",
    }
    path = _atomic_write(payload)
    apply_to_environment()
    return path


def delete_managed_google_oauth_settings() -> None:
    try:
        CONFIG_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    # Do not remove fallback env values supplied by systemd/Streamlit Secrets.
    fallback_id = _runtime_value("GOOGLE_OAUTH_CLIENT_ID", "")
    fallback_secret = _runtime_value("GOOGLE_OAUTH_CLIENT_SECRET", "")
    fallback_redirect = (
        _runtime_value("GOOGLE_OAUTH_REDIRECT_URI", "")
        or _runtime_value("QLDA_PUBLIC_BASE_URL", "")
        or DEFAULT_REDIRECT_URI
    ).rstrip("/")
    if fallback_id:
        os.environ["GOOGLE_OAUTH_CLIENT_ID"] = fallback_id
    if fallback_secret:
        os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = fallback_secret
    if fallback_redirect:
        os.environ["GOOGLE_OAUTH_REDIRECT_URI"] = fallback_redirect


def apply_to_environment() -> dict[str, Any]:
    """Expose effective encrypted settings to the existing Google client in-process.

    This changes only the current QLDA process environment. It does not edit qlda.env.
    On service restart this module reloads the encrypted settings file and reapplies it.
    """
    cfg = get_google_oauth_settings()
    if cfg.get("client_id"):
        os.environ["GOOGLE_OAUTH_CLIENT_ID"] = str(cfg["client_id"])
    if cfg.get("client_secret"):
        os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = str(cfg["client_secret"])
    if cfg.get("redirect_uri"):
        os.environ["GOOGLE_OAUTH_REDIRECT_URI"] = str(cfg["redirect_uri"])
    return cfg
