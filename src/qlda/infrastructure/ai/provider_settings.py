from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any


_DEFAULT_OPENAI_MODEL = "gpt-5-mini"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
_RETIRED_GEMINI_MODELS = {
    "gemini-2.5-flash",
    "models/gemini-2.5-flash",
}
_SECRET_FIELDS = {"openai_api_key", "gemini_api_key", "google_api_key"}


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


def _runtime_bool(name: str, default: bool = False) -> bool:
    value = _runtime_value(name, "")
    if not value:
        return bool(default)
    return value.lower() in {"1", "true", "yes", "on"}


def _settings_file() -> Path:
    explicit = _runtime_value("QLDA_APP_SETTINGS_FILE", "")
    if explicit:
        return Path(explicit).expanduser()
    settings_dir = _runtime_value("QLDA_SETTINGS_DIR", "")
    if settings_dir:
        return Path(settings_dir).expanduser() / "app_settings.json"
    shared = Path(_runtime_value("QLDA_SHARED_DIR", "/opt/qlda/shared")).expanduser()
    return shared / "config" / "app_settings.json"


def _fernet():
    secret = (
        _runtime_value("QLDA_SETTINGS_MASTER_KEY", "")
        or _runtime_value("QLDA_LOCAL_UPLOAD_SECRET", "")
    ).strip()
    if not secret:
        return None
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _load_saved() -> dict[str, Any]:
    candidates = [_settings_file(), Path.home() / ".qlda_xaydung" / "app_settings.json"]
    raw: dict[str, Any] = {}
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                raw = dict(value)
                break
        except Exception:
            continue
    encrypted = raw.pop("_encrypted_secrets", {})
    if isinstance(encrypted, dict) and encrypted:
        decryptor = _fernet()
        for field, token in encrypted.items():
            if field not in _SECRET_FIELDS:
                continue
            if not token or decryptor is None:
                raw[field] = ""
                continue
            try:
                raw[field] = decryptor.decrypt(str(token).encode("ascii")).decode("utf-8")
            except Exception:
                raw[field] = ""
    return raw


def normalize_gemini_model(value: Any) -> str:
    """Return a production-safe Gemini model id.

    Historical deployments may still persist the retired 2.5 Flash id in Admin
    settings or GEMINI_MODEL. Normalize those values at request time so a stale
    mutable setting cannot keep production AI down after the code is deployed.
    """
    raw = str(value or "").strip()
    if not raw or raw.lower() in {"auto", "default"}:
        return DEFAULT_GEMINI_MODEL
    lowered = raw.lower()
    if lowered in _RETIRED_GEMINI_MODELS:
        return DEFAULT_GEMINI_MODEL
    if lowered.startswith("models/"):
        raw = raw.split("/", 1)[1].strip()
    return raw or DEFAULT_GEMINI_MODEL


def get_provider_settings(preferred: str | None = None) -> dict[str, Any]:
    """Resolve AI provider settings without importing ``runtime_core``.

    Admin-managed encrypted settings keep the same on-disk contract as the
    historical settings store. When Admin management is disabled, environment
    and Streamlit secrets take precedence, matching existing deployments.
    """
    saved = _load_saved()
    managed = bool(saved.get("managed_ai", False))
    requested = str(preferred or "").strip().lower()
    if requested in {"gpt", "openai"}:
        provider = "openai"
    elif requested in {"google", "gemini"}:
        provider = "gemini"
    elif managed:
        provider = "gemini" if str(saved.get("ai_provider") or "").strip().lower() == "gemini" else "openai"
    else:
        explicit = _runtime_value("AI_PROVIDER", "").lower()
        if explicit in {"openai", "gemini"}:
            provider = explicit
        elif _runtime_value("GEMINI_API_KEY", ""):
            provider = "gemini"
        elif _runtime_value("OPENAI_API_KEY", ""):
            provider = "openai"
        else:
            provider = "gemini" if str(saved.get("ai_provider") or "").strip().lower() == "gemini" else "openai"

    if provider == "gemini":
        saved_key = str(saved.get("gemini_api_key") or "").strip()
        api_key = saved_key if managed else (_runtime_value("GEMINI_API_KEY", "") or saved_key)
        saved_model = str(saved.get("gemini_model") or "auto").strip() or "auto"
        configured_model = saved_model if managed else _runtime_value("GEMINI_MODEL", saved_model)
        model = normalize_gemini_model(configured_model)
        use_web = bool(saved.get("openai_web_search", False)) if managed else _runtime_bool(
            "GEMINI_WEB_SEARCH", _runtime_bool("AI_WEB_SEARCH", bool(saved.get("openai_web_search", False)))
        )
    else:
        saved_key = str(saved.get("openai_api_key") or "").strip()
        api_key = saved_key if managed else (_runtime_value("OPENAI_API_KEY", "") or saved_key)
        saved_model = str(saved.get("openai_model") or _DEFAULT_OPENAI_MODEL).strip() or _DEFAULT_OPENAI_MODEL
        model = saved_model if managed else _runtime_value("OPENAI_MODEL", saved_model)
        use_web = bool(saved.get("openai_web_search", False)) if managed else _runtime_bool(
            "OPENAI_WEB_SEARCH", _runtime_bool("AI_WEB_SEARCH", bool(saved.get("openai_web_search", False)))
        )

    return {
        "provider": provider,
        "api_key": str(api_key or "").strip(),
        "model": str(model or "").strip(),
        "use_web": bool(use_web),
        "managed": managed,
    }


__all__ = ["DEFAULT_GEMINI_MODEL", "get_provider_settings", "normalize_gemini_model"]
