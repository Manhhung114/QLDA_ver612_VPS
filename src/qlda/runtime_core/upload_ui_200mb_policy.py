from __future__ import annotations

"""QLDA upload/UI policy: keep the app simple and cap user files at 200 MB.

The V6.24 background-upload banners (BOQ/IPC/VO/Schedule) were useful while the
app exposed multi-GB direct uploads. They are intentionally hidden now. Existing
worker/backend code is left intact for compatibility, but the Streamlit UI no
longer renders those dedicated rows.

The active app-side upload cap is forced to 200 MB even if an old VPS environment
still contains QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB=2048.
"""

import os

PATCH_MARKER = "V7.6 UPLOAD UI 200MB POLICY V1"
MAX_UPLOAD_MB = 200


def _hidden_background_panel(*args, **kwargs) -> None:
    """No-op replacement for legacy multi-GB background upload panels."""
    return None


def install_upload_ui_200mb_policy() -> None:
    # Hide all legacy rows such as:
    #   "BOQ/IPC/VO lớn · xử lý nền V6.24.x"
    #   "Tải ... trực tiếp lên VPS · tối đa 2 GB"
    #   "Làm mới"
    # App imports these aliases after initialize_runtime(), so patching here is
    # early enough for the generated Streamlit entrypoint.
    try:
        import qlda.runtime_core.excel_background as bg

        for name in (
            "render_boq_background_panel",
            "render_ipc_background_panel",
            "render_vo_background_panel",
            "render_schedule_background_panel",
        ):
            if hasattr(bg, name):
                setattr(bg, name, _hidden_background_panel)
        bg._qlda_upload_ui_200mb_policy = PATCH_MARKER
    except Exception:
        pass

    # Force the running Streamlit process to advertise/use at most 200 MB for
    # any direct-ticket path that remains elsewhere in the application.
    os.environ["QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB"] = str(MAX_UPLOAD_MB)
    os.environ["QLDA_LOCAL_LEGACY_MAX_UPLOAD_MB"] = str(MAX_UPLOAD_MB)

    try:
        import qlda.runtime_core.drive_gateway as dg

        original = getattr(dg.DriveGatewayConfig, "from_values", None)
        if callable(original) and not getattr(dg.DriveGatewayConfig, "_qlda_200mb_cap_installed", False):
            original_func = original.__func__ if hasattr(original, "__func__") else original

            def from_values_200mb(
                cls,
                webapp_url: str = "",
                api_token: str = "",
                timeout=90,
                legacy_max_upload_mb=30,
                direct_max_upload_mb=200,
                max_upload_mb=None,
                **kwargs,
            ):
                # For local VPS storage, both upload paths are capped at 200 MB.
                backend = str(kwargs.get("backend", "drive") or "drive").strip().lower()
                if backend == "local":
                    legacy_max_upload_mb = min(MAX_UPLOAD_MB, int(legacy_max_upload_mb or MAX_UPLOAD_MB))
                    direct_max_upload_mb = MAX_UPLOAD_MB
                    if max_upload_mb is not None:
                        max_upload_mb = min(MAX_UPLOAD_MB, int(max_upload_mb or MAX_UPLOAD_MB))
                cfg = original_func(
                    cls,
                    webapp_url,
                    api_token,
                    timeout,
                    legacy_max_upload_mb,
                    direct_max_upload_mb,
                    max_upload_mb,
                    **kwargs,
                )
                if getattr(cfg, "backend", "") == "local":
                    cfg.legacy_max_upload_mb = min(MAX_UPLOAD_MB, int(cfg.legacy_max_upload_mb or MAX_UPLOAD_MB))
                    cfg.direct_max_upload_mb = MAX_UPLOAD_MB
                return cfg

            dg.DriveGatewayConfig.from_values = classmethod(from_values_200mb)
            dg.DriveGatewayConfig._qlda_200mb_cap_installed = True
    except Exception:
        pass

    # Local backend functions are also used inside the Streamlit process.
    try:
        import qlda.runtime_core.local_vps_backend as local

        local.direct_max_bytes = lambda: MAX_UPLOAD_MB * 1024 * 1024
        local.legacy_max_bytes = lambda: MAX_UPLOAD_MB * 1024 * 1024
        local._qlda_upload_200mb_policy = PATCH_MARKER
    except Exception:
        pass


__all__ = ["MAX_UPLOAD_MB", "install_upload_ui_200mb_policy"]
