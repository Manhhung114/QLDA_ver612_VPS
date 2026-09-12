from __future__ import annotations

from typing import Any


PATCH_MARKER = "V6.22 ADMIN SETTINGS RUNTIME BRIDGE V1"


def install_runtime_settings_bridge() -> None:
    """Route mutable VPS settings through settings_store without touching bootstrap secrets.

    The bridge is deliberately narrow. DATABASE_URL, auth/upload signing secrets,
    service ports, SSH and systemd controls remain environment-only values.
    """
    import settings_store as ss

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
    out: dict[str, Any] = {"marker": PATCH_MARKER, "local_vps": False, "multicore_excel": False}
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
