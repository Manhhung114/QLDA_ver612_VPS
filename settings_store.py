from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEGACY_CONFIG_DIR = Path.home() / ".qlda_xaydung"
LEGACY_APP_SETTINGS_FILE = LEGACY_CONFIG_DIR / "app_settings.json"
LEGACY_GOOGLE_FILE = LEGACY_CONFIG_DIR / "google_search.json"


def _default_config_dir() -> Path:
    explicit = str(os.environ.get("QLDA_SETTINGS_DIR", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    shared = Path(str(os.environ.get("QLDA_SHARED_DIR", "/opt/qlda/shared") or "/opt/qlda/shared")).expanduser()
    # Production VPS keeps mutable app settings outside the Git checkout and
    # outside $HOME (qlda-upload.service uses ProtectHome=true).
    if shared.exists() or str(shared).startswith("/opt/qlda/"):
        return shared / "config"
    return LEGACY_CONFIG_DIR


CONFIG_DIR = _default_config_dir()
APP_SETTINGS_FILE = Path(
    str(os.environ.get("QLDA_APP_SETTINGS_FILE", "") or (CONFIG_DIR / "app_settings.json"))
).expanduser()
AUDIT_FILE = Path(
    str(os.environ.get("QLDA_SETTINGS_AUDIT_FILE", "") or (CONFIG_DIR / "system_settings_audit.jsonl"))
).expanduser()

DEFAULT_SPECIFIED_SEARCH_DOMAINS = [
    "vanban.chinhphu.vn",
    "congbao.chinhphu.vn",
    "vbpl.vn",
    "moc.gov.vn",
    "tieuchuan.vsqi.gov.vn",
    "tieuchuanxaydung.vsqi.gov.vn",
    "thuvienphapluat.vn",
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "google_api_key": "",
    "google_cx": "",
    "ai_provider": "openai",
    "openai_api_key": "",
    "openai_model": "gpt-5-mini",
    "gemini_api_key": "",
    "gemini_model": "auto",
    "openai_web_search": False,
    "specified_search_domains": DEFAULT_SPECIFIED_SEARCH_DOMAINS,
    "drive_enabled": False,
    "drive_auto_upload": False,
    "drive_client_credentials_path": "",
    "drive_root_folder_id": "",
    "drive_root_folder_url": "",
    "drive_root_folder_name": "QLDA Xây dựng",
    # Admin-managed VPS runtime values. They remain opt-in so an existing
    # qlda.env keeps working unchanged until Admin explicitly saves this page.
    "managed_ai": False,
    "managed_storage": False,
    "managed_performance": False,
    "local_storage_root": "/opt/qlda/data",
    "local_trash_root": "/opt/qlda/data/.trash",
    "public_base_url": "",
    "local_legacy_max_upload_mb": 200,
    "local_direct_max_upload_mb": 2048,
    "local_session_ttl_hours": 12,
    "cpu_workers": 3,
    "parallel_excel_min_mb": 2.0,
    "parallel_min_sheets": 2,
    "mp_start_method": "forkserver",
}

_SECRET_FIELDS = {"openai_api_key", "gemini_api_key", "google_api_key"}
_MUTABLE_ENV_MAP: dict[str, tuple[str, str]] = {
    # env name: (settings key, management group)
    "QLDA_LOCAL_STORAGE_ROOT": ("local_storage_root", "managed_storage"),
    "QLDA_LOCAL_TRASH_ROOT": ("local_trash_root", "managed_storage"),
    "QLDA_PUBLIC_BASE_URL": ("public_base_url", "managed_storage"),
    "QLDA_LOCAL_LEGACY_MAX_UPLOAD_MB": ("local_legacy_max_upload_mb", "managed_storage"),
    "QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB": ("local_direct_max_upload_mb", "managed_storage"),
    "QLDA_LOCAL_SESSION_TTL_HOURS": ("local_session_ttl_hours", "managed_storage"),
    "QLDA_CPU_WORKERS": ("cpu_workers", "managed_performance"),
    "QLDA_PARALLEL_EXCEL_MIN_MB": ("parallel_excel_min_mb", "managed_performance"),
    "QLDA_PARALLEL_MIN_SHEETS": ("parallel_min_sheets", "managed_performance"),
    "QLDA_MP_START_METHOD": ("mp_start_method", "managed_performance"),
}


def _runtime_value_raw(name: str, default: str = "") -> str:
    """Environment first, then root Streamlit Secret, without exposing values."""
    value = str(os.environ.get(name, "") or "").strip()
    if value:
        return value
    try:
        import streamlit as st

        value = str(st.secrets.get(name, "") or "").strip()
    except Exception:
        value = ""
    return value or default


def _runtime_bool_raw(name: str, default: bool = False) -> bool:
    value = _runtime_value_raw(name, "")
    if not value:
        return bool(default)
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _clean_domains(values) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        d = str(value or "").strip().lower()
        d = d.removeprefix("https://").removeprefix("http://").split("/", 1)[0].strip()
        if d.startswith("www."):
            d = d[4:]
        if d and "." in d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _master_secret() -> str:
    # Dedicated key is preferred. The existing long local-upload secret is a
    # safe production fallback and already lives only in /opt/qlda/shared/qlda.env.
    return (
        _runtime_value_raw("QLDA_SETTINGS_MASTER_KEY", "")
        or _runtime_value_raw("QLDA_LOCAL_UPLOAD_SECRET", "")
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


def _decrypt_secrets(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw)
    encrypted = data.pop("_encrypted_secrets", {})
    if not isinstance(encrypted, dict) or not encrypted:
        return data
    fernet = _fernet()
    for field, token in encrypted.items():
        if field not in _SECRET_FIELDS:
            continue
        if not token or fernet is None:
            data[field] = ""
            continue
        try:
            data[field] = fernet.decrypt(str(token).encode("ascii")).decode("utf-8")
        except Exception:
            # Fail closed. A changed master key must never expose ciphertext or
            # silently treat it as a usable provider credential.
            data[field] = ""
    return data


def _serialize_settings(data: dict[str, Any]) -> dict[str, Any]:
    raw = dict(data)
    encrypted: dict[str, str] = {}
    fernet = _fernet()
    if fernet is not None:
        for field in _SECRET_FIELDS:
            value = str(raw.pop(field, "") or "").strip()
            if value:
                encrypted[field] = fernet.encrypt(value.encode("utf-8")).decode("ascii")
        raw["_encrypted_secrets"] = encrypted
        raw["_secret_storage"] = "fernet-sha256-derived"
    else:
        # Development fallback only. Production VPS has QLDA_LOCAL_UPLOAD_SECRET,
        # so real provider keys are encrypted at rest. File mode remains 0600.
        raw["_secret_storage"] = "file-0600"
    return raw


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _load_saved_settings() -> dict[str, Any]:
    if APP_SETTINGS_FILE.exists():
        return _decrypt_secrets(_read_json_file(APP_SETTINGS_FILE))
    # Seamless migration from historical ~/.qlda_xaydung on the Streamlit
    # process. The upload service cannot access $HOME, therefore all new saves
    # land in /opt/qlda/shared/config on VPS.
    if LEGACY_APP_SETTINGS_FILE.exists() and LEGACY_APP_SETTINGS_FILE != APP_SETTINGS_FILE:
        return _decrypt_secrets(_read_json_file(LEGACY_APP_SETTINGS_FILE))
    return {}


def _runtime_provider(cfg: dict[str, Any]) -> str:
    if bool(cfg.get("managed_ai")):
        return "gemini" if str(cfg.get("ai_provider") or "").strip().lower() == "gemini" else "openai"
    explicit = _runtime_value_raw("AI_PROVIDER", "").strip().lower()
    if explicit in {"openai", "gemini"}:
        return explicit
    if _runtime_value_raw("GEMINI_API_KEY", ""):
        return "gemini"
    if _runtime_value_raw("OPENAI_API_KEY", ""):
        return "openai"
    return "gemini" if str(cfg.get("ai_provider") or "").strip().lower() == "gemini" else "openai"


def load_app_settings() -> dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    data["specified_search_domains"] = list(DEFAULT_SPECIFIED_SEARCH_DOMAINS)
    saved = _load_saved_settings()
    if saved:
        data.update(saved)

    # Migrate Google settings from V4.0.5/V4.0.6 without deleting the legacy file.
    if (not data.get("google_api_key") or not data.get("google_cx")) and LEGACY_GOOGLE_FILE.exists():
        try:
            legacy = json.loads(LEGACY_GOOGLE_FILE.read_text(encoding="utf-8"))
            data["google_api_key"] = data.get("google_api_key") or str(legacy.get("api_key", "")).strip()
            data["google_cx"] = data.get("google_cx") or str(legacy.get("cx", "")).strip()
        except Exception:
            pass

    data["specified_search_domains"] = _clean_domains(data.get("specified_search_domains")) or list(DEFAULT_SPECIFIED_SEARCH_DOMAINS)

    data["ai_provider"] = _runtime_provider(data)
    if not bool(data.get("managed_ai")):
        data["openai_model"] = _runtime_value_raw("OPENAI_MODEL", str(data.get("openai_model") or "gpt-5-mini")) or "gpt-5-mini"
        data["gemini_model"] = _runtime_value_raw("GEMINI_MODEL", str(data.get("gemini_model") or "auto")) or "auto"
        web_default = bool(data.get("openai_web_search", False))
        if _runtime_value_raw("AI_WEB_SEARCH", ""):
            data["openai_web_search"] = _runtime_bool_raw("AI_WEB_SEARCH", web_default)
        elif data["ai_provider"] == "gemini" and _runtime_value_raw("GEMINI_WEB_SEARCH", ""):
            data["openai_web_search"] = _runtime_bool_raw("GEMINI_WEB_SEARCH", web_default)
        elif data["ai_provider"] == "openai" and _runtime_value_raw("OPENAI_WEB_SEARCH", ""):
            data["openai_web_search"] = _runtime_bool_raw("OPENAI_WEB_SEARCH", web_default)
        else:
            data["openai_web_search"] = web_default
    else:
        data["openai_model"] = str(data.get("openai_model") or "gpt-5-mini").strip() or "gpt-5-mini"
        data["gemini_model"] = str(data.get("gemini_model") or "auto").strip() or "auto"
        data["openai_web_search"] = bool(data.get("openai_web_search", False))

    data["drive_enabled"] = bool(data.get("drive_enabled", False))
    data["drive_auto_upload"] = bool(data.get("drive_auto_upload", False))
    data["drive_root_folder_name"] = str(data.get("drive_root_folder_name") or "QLDA Xây dựng").strip() or "QLDA Xây dựng"
    return data


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except Exception:
            pass
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass
    return path


def save_app_settings(settings: dict[str, Any]) -> Path:
    current = load_app_settings()
    current.update(settings or {})
    current["specified_search_domains"] = _clean_domains(current.get("specified_search_domains")) or list(DEFAULT_SPECIFIED_SEARCH_DOMAINS)
    return _atomic_write_json(APP_SETTINGS_FILE, _serialize_settings(current))


def append_settings_audit(actor: str, action: str, changed_keys: list[str] | tuple[str, ...]) -> None:
    """Append metadata only; values and secrets are never written to the audit."""
    entry = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "actor": str(actor or "admin").strip() or "admin",
        "action": str(action or "update").strip() or "update",
        "changed_keys": sorted({str(x) for x in changed_keys if str(x).strip()}),
    }
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        os.chmod(AUDIT_FILE, 0o600)
    except Exception:
        pass


def read_settings_audit(limit: int = 100) -> list[dict[str, Any]]:
    if not AUDIT_FILE.exists():
        return []
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()[-max(1, min(int(limit), 500)):]
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except Exception:
            continue
    return rows


def get_specified_search_domains() -> tuple[str, ...]:
    return tuple(load_app_settings().get("specified_search_domains") or DEFAULT_SPECIFIED_SEARCH_DOMAINS)


def _effective_secret(cfg: dict[str, Any], field: str, env_name: str) -> str:
    saved = str(cfg.get(field, "") or "").strip()
    if bool(cfg.get("managed_ai")):
        return saved
    return (_runtime_value_raw(env_name, "") or saved).strip()


def get_openai_runtime_settings() -> dict[str, Any]:
    cfg = load_app_settings()
    managed = bool(cfg.get("managed_ai"))
    if managed:
        use_web = bool(cfg.get("openai_web_search", False))
        model = str(cfg.get("openai_model") or "gpt-5-mini").strip() or "gpt-5-mini"
    else:
        use_web = _runtime_bool_raw("OPENAI_WEB_SEARCH", bool(cfg.get("openai_web_search", False)))
        model = (_runtime_value_raw("OPENAI_MODEL", "") or str(cfg.get("openai_model", "gpt-5-mini"))).strip() or "gpt-5-mini"
    return {
        "api_key": _effective_secret(cfg, "openai_api_key", "OPENAI_API_KEY"),
        "model": model,
        "use_web": use_web,
    }


def get_gemini_runtime_settings() -> dict[str, Any]:
    cfg = load_app_settings()
    managed = bool(cfg.get("managed_ai"))
    if managed:
        use_web = bool(cfg.get("openai_web_search", False))
        model = str(cfg.get("gemini_model") or "auto").strip() or "auto"
    else:
        use_web = _runtime_bool_raw("GEMINI_WEB_SEARCH", bool(cfg.get("openai_web_search", False)))
        model = (_runtime_value_raw("GEMINI_MODEL", "") or str(cfg.get("gemini_model", "auto"))).strip() or "auto"
    return {
        "api_key": _effective_secret(cfg, "gemini_api_key", "GEMINI_API_KEY"),
        "model": model,
        "use_web": use_web,
    }


def get_ai_runtime_settings() -> dict[str, Any]:
    cfg = load_app_settings()
    provider = _runtime_provider(cfg)
    if provider == "gemini":
        result = get_gemini_runtime_settings()
        result["provider"] = "gemini"
        return result
    result = get_openai_runtime_settings()
    result["provider"] = "openai"
    return result


def get_runtime_value(name: str, default: str = "") -> str:
    """Return an Admin-managed mutable runtime value or the env fallback.

    DATABASE_URL, upload signing secrets, bootstrap codes, ports and Linux-level
    controls are deliberately absent from _MUTABLE_ENV_MAP and therefore remain
    bootstrap-only values outside the web UI.
    """
    mapping = _MUTABLE_ENV_MAP.get(str(name or ""))
    if mapping:
        key, group = mapping
        cfg = load_app_settings()
        if bool(cfg.get(group)):
            value = cfg.get(key)
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value if value is not None else default).strip()
    return _runtime_value_raw(name, default)


def effective_settings_sources() -> dict[str, str]:
    cfg = load_app_settings()
    return {
        "ai": "App / Admin" if bool(cfg.get("managed_ai")) else "qlda.env / Streamlit Secrets",
        "storage": "App / Admin" if bool(cfg.get("managed_storage")) else "qlda.env",
        "performance": "App / Admin" if bool(cfg.get("managed_performance")) else "qlda.env",
        "secret_storage": "Mã hóa Fernet" if encryption_available() else "File 0600 (chưa có master key)",
    }
