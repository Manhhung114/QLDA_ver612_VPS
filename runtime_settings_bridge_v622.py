from __future__ import annotations

from typing import Any


PATCH_MARKER = "V6.22 ADMIN SETTINGS RUNTIME BRIDGE V2"


def install_runtime_settings_bridge() -> None:
    """Route mutable VPS settings through settings_store without touching bootstrap secrets.

    The bridge is deliberately narrow. DATABASE_URL, auth/upload signing secrets,
    service ports, SSH and systemd controls remain environment-only values.
    """
    import settings_store as ss

    # AI assistants historically construct provider settings from environment
    # variables. Patch those constructors so Admin-managed encrypted settings are
    # honored by every project/file/contract AI call while preserving env fallback.
    try:
        import ai_service as ai

        if not getattr(ai, "_qlda_admin_settings_bridge_installed", False):
            def _openai_from_runtime(cls):
                value = ss.get_openai_runtime_settings()
                return cls(
                    api_key=str(value.get("api_key") or "").strip(),
                    model=str(value.get("model") or "gpt-5-mini").strip() or "gpt-5-mini",
                    use_web=bool(value.get("use_web", False)),
                )

            def _gemini_from_runtime(cls):
                value = ss.get_gemini_runtime_settings()
                return cls(
                    api_key=str(value.get("api_key") or "").strip(),
                    model=str(value.get("model") or "auto").strip() or "auto",
                    use_web=bool(value.get("use_web", False)),
                )

            ai.AISettings.from_env = classmethod(_openai_from_runtime)
            ai.GeminiSettings.from_env = classmethod(_gemini_from_runtime)
            ai._qlda_admin_settings_bridge_installed = True
            ai._qlda_admin_settings_bridge_marker = PATCH_MARKER
    except Exception:
        # The file upload service does not need the AI module. Failure to import
        # an optional provider must not prevent local file service startup.
        pass

    try:
        import local_vps_backend_v622 as lb

        if not getattr(lb, "_qlda_admin_settings_bridge_installed", False):
            def _managed_env(name: str, default: str = "") -> str:
                return ss.get_runtime_value(str(name or ""), str(default or ""))

            lb._env = _managed_env
            lb._qlda_admin_settings_bridge_installed = True
            lb._qlda_admin_settings_bridge_marker = PATCH_MARKER
    except Exception:
        # Streamlit deployments that do not use the local VPS backend must still
        # be able to start and use AI/settings normally.
        pass

    try:
        import multicore_excel_v622 as mc

        if not getattr(mc, "_qlda_admin_settings_bridge_installed", False):
            def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
                try:
                    value = int(float(ss.get_runtime_value(name, str(default))))
                except Exception:
                    value = int(default)
                return max(int(minimum), min(int(maximum), value))

            def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
                try:
                    value = float(ss.get_runtime_value(name, str(default)))
                except Exception:
                    value = float(default)
                return max(float(minimum), min(float(maximum), value))

            original_start_method = mc._start_method

            def _start_method() -> str:
                import multiprocessing as mp

                available = set(mp.get_all_start_methods())
                requested = ss.get_runtime_value("QLDA_MP_START_METHOD", "forkserver").strip().lower()
                if requested in available:
                    return requested
                return original_start_method()

            mc._env_int = _env_int
            mc._env_float = _env_float
            mc._start_method = _start_method
            mc._qlda_admin_settings_bridge_installed = True
            mc._qlda_admin_settings_bridge_marker = PATCH_MARKER
    except Exception:
        pass


def runtime_bridge_status() -> dict[str, Any]:
    out: dict[str, Any] = {
        "marker": PATCH_MARKER,
        "ai": False,
        "local_vps": False,
        "multicore_excel": False,
    }
    try:
        import ai_service as ai
        out["ai"] = bool(getattr(ai, "_qlda_admin_settings_bridge_installed", False))
    except Exception:
        pass
    try:
        import local_vps_backend_v622 as lb
        out["local_vps"] = bool(getattr(lb, "_qlda_admin_settings_bridge_installed", False))
    except Exception:
        pass
    try:
        import multicore_excel_v622 as mc
        out["multicore_excel"] = bool(getattr(mc, "_qlda_admin_settings_bridge_installed", False))
    except Exception:
        pass
    return out
