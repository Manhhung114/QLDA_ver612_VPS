from __future__ import annotations

import os
from typing import Iterable


_SECRET_KEYS: tuple[str, ...] = (
    "DATABASE_URL",
    "QLDA_DATABASE_URL",
    "POSTGRES_URL",
    "QLDA_DRIVE_WEBAPP_URL",
    "QLDA_DRIVE_API_TOKEN",
    "AI_PROVIDER",
    "AI_WEB_SEARCH",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GEMINI_WEB_SEARCH",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_WEB_SEARCH",
    "GOOGLE_SEARCH_API_KEY",
    "GOOGLE_CSE_API_KEY",
    "GOOGLE_SEARCH_CX",
    "GOOGLE_CSE_CX",
)


def _secret_value(name: str) -> str:
    """Read one root Streamlit secret without ever logging its value."""
    try:
        import streamlit as st

        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value or "").strip()


def apply_streamlit_secrets_to_env(keys: Iterable[str] = _SECRET_KEYS) -> dict[str, bool]:
    """Bridge Streamlit Community Cloud root secrets to env-based modules.

    Some runtime modules read ``os.environ`` directly. Streamlit Community Cloud
    exposes root values through ``st.secrets``; this bridge runs before those
    modules are imported so the existing Gemini, Drive and database behaviour is
    preserved without exposing secret values.
    """
    loaded: dict[str, bool] = {}
    for name in keys:
        current = str(os.environ.get(name, "") or "").strip()
        if current:
            loaded[name] = True
            continue
        value = _secret_value(name)
        if value:
            os.environ[name] = value
            loaded[name] = True
        else:
            loaded[name] = False

    # Preserve the user's long-standing Gemini configuration. If no provider is
    # explicitly selected but a Gemini key exists, choose Gemini rather than the
    # historical OpenAI default.
    provider = str(os.environ.get("AI_PROVIDER", "") or "").strip().lower()
    if provider not in {"openai", "gemini"}:
        if str(os.environ.get("GEMINI_API_KEY", "") or "").strip():
            os.environ["AI_PROVIDER"] = "gemini"
            loaded["AI_PROVIDER"] = True
        elif str(os.environ.get("OPENAI_API_KEY", "") or "").strip():
            os.environ["AI_PROVIDER"] = "openai"
            loaded["AI_PROVIDER"] = True

    if str(os.environ.get("AI_PROVIDER", "") or "").strip().lower() == "gemini":
        os.environ.setdefault("GEMINI_MODEL", "auto")

    return loaded
