from __future__ import annotations

from typing import Any


PATCH_MARKER = "V6.22 ADMIN SETTINGS RUNTIME BRIDGE V3-NO-AI"


def install_runtime_settings_bridge() -> None:
    """Route non-AI mutable VPS settings through the legacy settings store.

    AI settings are now resolved natively by
    ``qlda.infrastructure.ai.provider_settings``. This compatibility bridge is
    intentionally limited to storage/performance slices that have not yet been
    migrated out of ``runtime_core``.
    """
    import qlda.runtime_core.settings_store as ss

    try:
        import qlda.runtime_core.local_vps_backend as lb
        if not getattr(lb, "_qlda_admin_settings_bridge_installed", False):
            def _managed_env(name: str, default: str = "") -> str:
                return ss.get_runtime_value(str(name or ""), str(default or ""))

            lb._env = _managed_env
            lb._qlda_admin_settings_bridge_installed = True
            lb._qlda_admin_settings_bridge_marker = PATCH_MARKER
    except Exception:
        pass

    try:
        import qlda.runtime_core.multicore_excel as mc
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
                return max(float(minimum), min(int(maximum), value))

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
        import qlda.runtime_core.local_vps_backend as lb
        out["local_vps"] = bool(getattr(lb, "_qlda_admin_settings_bridge_installed", False))
    except Exception:
        pass
    try:
        import qlda.runtime_core.multicore_excel as mc
        out["multicore_excel"] = bool(getattr(mc, "_qlda_admin_settings_bridge_installed", False))
    except Exception:
        pass
    return out
