from __future__ import annotations

"""QLDA upload/UI policy: keep the app simple and cap user files at 500 MB.

The V6.24 background-upload banners (BOQ/IPC/VO/Schedule) were useful while the
app exposed multi-GB direct uploads. They remain intentionally hidden. Existing
worker/backend code is left intact for compatibility, while all active local VPS
upload paths are normalized to a 500 MB per-file limit.

The module name is retained temporarily for backward compatibility with the V7.6
runtime composition; the active policy itself is 500 MB.
"""

import os

PATCH_MARKER = "V7.6 UPLOAD UI 500MB POLICY V2"
MAX_UPLOAD_MB = 500


def _hidden_background_panel(*args, **kwargs) -> None:
    """No-op replacement for legacy multi-GB background upload panels."""
    return None


def install_upload_ui_200mb_policy() -> None:
    """Install the current 500 MB upload policy.

    The historical function name is kept so existing V7.6 imports remain stable.
    """
    # Hide all legacy rows such as:
    #   "BOQ/IPC/VO lớn · xử lý nền V6.24.x"
    #   "Tải ... trực tiếp lên VPS · tối đa 2 GB"
    #   "Làm mới"
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
        bg._qlda_upload_ui_500mb_policy = PATCH_MARKER
    except Exception:
        pass

    # Force the running Streamlit process to advertise/use 500 MB for all
    # local direct-ticket and legacy upload paths.
    os.environ["QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB"] = str(MAX_UPLOAD_MB)
    os.environ["QLDA_LOCAL_LEGACY_MAX_UPLOAD_MB"] = str(MAX_UPLOAD_MB)

    try:
        import qlda.runtime_core.drive_gateway as dg

        original = getattr(dg.DriveGatewayConfig, "from_values", None)
        if callable(original) and not getattr(dg.DriveGatewayConfig, "_qlda_500mb_cap_installed", False):
            original_func = original.__func__ if hasattr(original, "__func__") else original

            def from_values_500mb(
                cls,
                webapp_url: str = "",
                api_token: str = "",
                timeout=90,
                legacy_max_upload_mb=30,
                direct_max_upload_mb=500,
                max_upload_mb=None,
                **kwargs,
            ):
                backend = str(kwargs.get("backend", "drive") or "drive").strip().lower()
                if backend == "local":
                    legacy_max_upload_mb = MAX_UPLOAD_MB
                    direct_max_upload_mb = MAX_UPLOAD_MB
                    max_upload_mb = MAX_UPLOAD_MB
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
                    cfg.legacy_max_upload_mb = MAX_UPLOAD_MB
                    cfg.direct_max_upload_mb = MAX_UPLOAD_MB
                return cfg

            dg.DriveGatewayConfig.from_values = classmethod(from_values_500mb)
            dg.DriveGatewayConfig._qlda_500mb_cap_installed = True
    except Exception:
        pass

    # Local backend functions are also used inside the Streamlit process.
    try:
        import qlda.runtime_core.local_vps_backend as local

        local.direct_max_bytes = lambda: MAX_UPLOAD_MB * 1024 * 1024
        local.legacy_max_bytes = lambda: MAX_UPLOAD_MB * 1024 * 1024
        local._qlda_upload_500mb_policy = PATCH_MARKER
    except Exception:
        pass


__all__ = ["MAX_UPLOAD_MB", "install_upload_ui_200mb_policy"]
