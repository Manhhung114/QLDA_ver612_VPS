from __future__ import annotations

"""Persistent company-logo watermark and Admin branding controls.

The logo is stored outside the Git checkout so it survives deploys.  The runtime
renders it as a faint, centered page watermark behind the working area on every
sheet.  It never captures pointer events and therefore cannot block the UI.
"""

import base64
import html
import os
from pathlib import Path
from typing import Any

import qlda.runtime_core.settings_store as ss

_MAX_LOGO_BYTES = 2 * 1024 * 1024
_ALLOWED = {
    "png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    "jpg": ("image/jpeg", b"\xff\xd8\xff"),
    "webp": ("image/webp", b"RIFF"),
}


def _branding_dir() -> Path:
    explicit = str(os.environ.get("QLDA_BRANDING_DIR", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return ss.CONFIG_DIR.parent / "branding"


def _logo_candidates() -> list[Path]:
    root = _branding_dir()
    return [root / "company_logo.png", root / "company_logo.jpg", root / "company_logo.webp"]


def current_logo_path() -> Path | None:
    for path in _logo_candidates():
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _detect_logo_type(payload: bytes) -> tuple[str, str]:
    if payload.startswith(_ALLOWED["png"][1]):
        return "png", "image/png"
    if payload.startswith(_ALLOWED["jpg"][1]):
        return "jpg", "image/jpeg"
    if len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "webp", "image/webp"
    raise ValueError("Logo chỉ hỗ trợ PNG, JPG/JPEG hoặc WEBP.")


def save_company_logo(payload: bytes) -> Path:
    data = bytes(payload or b"")
    if not data:
        raise ValueError("File logo đang trống.")
    if len(data) > _MAX_LOGO_BYTES:
        raise ValueError("Logo tối đa 2 MB. Hãy giảm kích thước ảnh trước khi tải lên.")
    ext, _mime = _detect_logo_type(data)
    root = _branding_dir()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"company_logo.{ext}"
    temp = root / f".company_logo.{ext}.tmp"
    temp.write_bytes(data)
    try:
        os.chmod(temp, 0o640)
    except OSError:
        pass
    temp.replace(target)
    for other in _logo_candidates():
        if other != target:
            other.unlink(missing_ok=True)
    return target


def delete_company_logo() -> bool:
    removed = False
    for path in _logo_candidates():
        if path.exists():
            path.unlink(missing_ok=True)
            removed = True
    return removed


def _logo_data_uri(path: Path) -> str:
    payload = path.read_bytes()
    ext, mime = _detect_logo_type(payload)
    _ = ext
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _watermark_css(data_uri: str | None, *, opacity: float, width_vw: int) -> str:
    opacity = max(0.02, min(float(opacity), 0.20))
    width_vw = max(18, min(int(width_vw), 60))
    background = f'url("{html.escape(data_uri, quote=True)}")' if data_uri else "none"
    return f"""
<style id="qlda-company-watermark-style">
[data-testid="stAppViewContainer"]::before{{
  content:"";
  position:fixed;
  inset:0;
  pointer-events:none;
  z-index:0;
  background-image:{background};
  background-repeat:no-repeat;
  background-position:center 58%;
  background-size:min({width_vw}vw, 560px) auto;
  opacity:{opacity:.3f};
}}
[data-testid="stAppViewContainer"] > *{{
  position:relative;
}}
[data-testid="stAppViewContainer"] [data-testid="stHeader"],
[data-testid="stAppViewContainer"] [data-testid="stSidebar"],
[data-testid="stAppViewContainer"] [data-testid="stMain"]{{
  z-index:1;
}}
@media(max-width:760px){{
  [data-testid="stAppViewContainer"]::before{{
    background-position:center 56%;
    background-size:min({max(width_vw + 12, 42)}vw, 420px) auto;
  }}
}}
</style>
"""


def render_company_logo_watermark(st: Any) -> None:
    """Render/clear the watermark CSS on every Streamlit rerun."""
    cfg = ss.load_app_settings()
    path = current_logo_path()
    enabled = bool(cfg.get("company_logo_enabled", True)) and path is not None
    data_uri: str | None = None
    if enabled and path is not None:
        try:
            data_uri = _logo_data_uri(path)
        except Exception:
            data_uri = None
    opacity = float(cfg.get("company_logo_opacity", 0.055) or 0.055)
    width_vw = int(cfg.get("company_logo_width_vw", 34) or 34)
    st.markdown(
        _watermark_css(data_uri, opacity=opacity, width_vw=width_vw),
        unsafe_allow_html=True,
    )


def render_company_logo_settings(st: Any, actor: str = "") -> None:
    """Admin UI for upload/delete and watermark appearance."""
    cfg = ss.load_app_settings()
    st.markdown("#### 🏢 Logo công ty · watermark nền")
    st.caption(
        "Logo hiển thị chìm giữa nền trang trên tất cả các sheet, không nằm ở một góc và không cản thao tác. "
        "File được lưu tập trung trên VPS và giữ nguyên qua các lần deploy."
    )

    current = current_logo_path()
    if current is not None:
        st.image(str(current), width=180, caption=f"Logo hiện tại · {current.name}")
    else:
        st.info("Chưa có logo công ty.")

    upload = st.file_uploader(
        "Tải logo công ty",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=False,
        key="qlda_company_logo_upload_v1",
        help="PNG nền trong suốt cho hiệu ứng watermark đẹp nhất. Tối đa 2 MB.",
    )

    c1, c2 = st.columns(2)
    opacity_pct = c1.slider(
        "Độ mờ logo (%)",
        min_value=2,
        max_value=20,
        value=max(2, min(20, int(round(float(cfg.get("company_logo_opacity", 0.055) or 0.055) * 100)))),
        step=1,
        key="qlda_company_logo_opacity_v1",
    )
    width_vw = c2.slider(
        "Kích thước logo nền (%)",
        min_value=18,
        max_value=60,
        value=max(18, min(60, int(cfg.get("company_logo_width_vw", 34) or 34))),
        step=1,
        key="qlda_company_logo_width_v1",
    )

    save_col, delete_col = st.columns(2)
    if save_col.button("💾 Lưu logo / hiển thị", type="primary", use_container_width=True, key="qlda_company_logo_save_v1"):
        try:
            changed = ["company_logo_enabled", "company_logo_opacity", "company_logo_width_vw"]
            if upload is not None:
                save_company_logo(upload.getvalue())
                changed.append("company_logo_file")
            if current_logo_path() is None:
                raise ValueError("Hãy chọn file logo trước khi bật hiển thị.")
            ss.save_app_settings({
                "company_logo_enabled": True,
                "company_logo_opacity": float(opacity_pct) / 100.0,
                "company_logo_width_vw": int(width_vw),
            })
            ss.append_settings_audit(actor, "update_company_logo", changed)
            st.success("Đã lưu logo. Logo sẽ hiển thị chìm dưới nền trên tất cả các sheet.")
            st.rerun()
        except Exception as exc:
            st.error(f"Không thể lưu logo: {exc}")

    if delete_col.button(
        "🗑️ Xóa logo",
        use_container_width=True,
        disabled=current is None,
        key="qlda_company_logo_delete_v1",
    ):
        try:
            delete_company_logo()
            ss.save_app_settings({"company_logo_enabled": False})
            ss.append_settings_audit(actor, "delete_company_logo", ["company_logo_enabled", "company_logo_file"])
            st.success("Đã xóa logo. Watermark đã được tắt trên toàn app.")
            st.rerun()
        except Exception as exc:
            st.error(f"Không thể xóa logo: {exc}")


def install_company_branding_runtime(st: Any | None = None) -> None:
    """Install settings integration once and refresh watermark every rerun."""
    if st is None:
        import streamlit as st  # type: ignore[no-redef]

    # Patch the Admin system-settings renderer before app.py imports it.  This
    # keeps branding configuration in the central settings console without
    # touching individual sheets.
    try:
        import qlda.runtime_core.system_settings as system_settings

        original = system_settings.render_system_settings_admin
        if not getattr(original, "_qlda_company_branding_wrapped", False):
            def wrapped(st_arg: Any, db: Any, *, is_admin: bool = False, actor: str = "") -> None:
                original(st_arg, db, is_admin=is_admin, actor=actor)
                if bool(is_admin):
                    st_arg.divider()
                    render_company_logo_settings(st_arg, actor)

            wrapped._qlda_company_branding_wrapped = True  # type: ignore[attr-defined]
            system_settings.render_system_settings_admin = wrapped
    except Exception:
        # Branding must never prevent the core app from starting.
        pass

    render_company_logo_watermark(st)


__all__ = [
    "current_logo_path",
    "delete_company_logo",
    "install_company_branding_runtime",
    "render_company_logo_settings",
    "render_company_logo_watermark",
    "save_company_logo",
]
