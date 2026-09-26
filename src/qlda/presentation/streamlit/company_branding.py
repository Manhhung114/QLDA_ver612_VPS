from __future__ import annotations

"""Persistent company-logo branding and Admin controls.

The logo is stored outside the Git checkout so it survives deploys. At runtime
it is rendered as the first visual block in the shared Streamlit sidebar,
therefore it appears above the Logout control and remains consistent on every
sheet in the application.
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
    _ext, mime = _detect_logo_type(payload)
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _sidebar_logo_css(
    data_uri: str | None,
    *,
    opacity_pct: int,
    height_px: int,
    gap_px: int,
) -> str:
    """Build CSS that places the logo above the Logout control in the sidebar."""
    opacity_pct = max(0, min(int(opacity_pct), 100))
    height_px = max(40, min(int(height_px), 220))
    gap_px = max(0, min(int(gap_px), 40))

    # Always disable the former H3-based location so an old/duplicate logo can
    # never remain below Logout after this placement change.
    legacy_reset = """
[data-testid="stSidebar"] h3::before{
  content:none!important;
  display:none!important;
  background-image:none!important;
}
"""

    if not data_uri or opacity_pct <= 0:
        return f"""
<style id="qlda-company-logo-style">
{legacy_reset}
[data-testid="stSidebarContent"]::before{{
  content:none!important;
  display:none!important;
  background-image:none!important;
}}
</style>
"""

    background = f'url("{html.escape(data_uri, quote=True)}")'
    opacity = float(opacity_pct) / 100.0

    return f"""
<style id="qlda-company-logo-style">
{legacy_reset}
/*
  The company logo is the first block in Streamlit's sidebar content. This
  guarantees the visual order:
      LOGO -> Logout -> QLDA Xây dựng -> project tools/navigation.
  The element is decorative and never intercepts clicks.
*/
[data-testid="stSidebarContent"]::before{{
  content:"";
  display:block;
  flex:0 0 auto;
  width:100%;
  height:{height_px}px;
  margin:4px 0 {gap_px}px 0;
  padding:0;
  box-sizing:border-box;
  background-image:{background};
  background-repeat:no-repeat;
  background-position:center center;
  background-size:contain;
  opacity:{opacity:.3f};
  pointer-events:none;
}}

/* Keep the sidebar's first real widget close to the logo without overlap. */
[data-testid="stSidebarContent"] > :first-child{{
  margin-top:0!important;
}}

@media(max-width:760px){{
  [data-testid="stSidebarContent"]::before{{
    height:{max(48, min(height_px, 150))}px;
    margin-top:2px;
    margin-bottom:{min(gap_px, 18)}px;
  }}
}}
</style>
"""


def render_company_logo_watermark(st: Any) -> None:
    """Compatibility name: refresh the global sidebar company logo CSS."""
    cfg = ss.load_app_settings()
    path = current_logo_path()
    enabled = bool(cfg.get("company_logo_enabled", True)) and path is not None

    data_uri: str | None = None
    if enabled and path is not None:
        try:
            data_uri = _logo_data_uri(path)
        except Exception:
            data_uri = None

    opacity_pct = int(cfg.get("company_logo_opacity_pct", 100) or 0)
    height_px = int(cfg.get("company_logo_height_px", 92) or 92)
    gap_px = int(cfg.get("company_logo_gap_px", 10) or 0)

    st.markdown(
        _sidebar_logo_css(
            data_uri,
            opacity_pct=opacity_pct,
            height_px=height_px,
            gap_px=gap_px,
        ),
        unsafe_allow_html=True,
    )


def render_company_sidebar_logo(st: Any) -> None:
    """Explicit alias for callers/tests that describe the sidebar placement."""
    render_company_logo_watermark(st)


def render_company_logo_settings(st: Any, actor: str = "") -> None:
    """Admin UI for upload/delete and sidebar-logo appearance."""
    cfg = ss.load_app_settings()

    st.markdown("#### 🏢 Logo công ty")
    st.caption(
        "Logo hiển thị ở đầu sidebar, phía trên nút ‘Đăng xuất’, trên toàn bộ app. "
        "File được lưu tập trung trên VPS và không mất khi deploy lại."
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
        key="qlda_company_logo_upload_v3",
        help="Khuyến nghị PNG nền trong suốt. Dung lượng tối đa 2 MB.",
    )

    current_opacity = int(cfg.get("company_logo_opacity_pct", 100) or 0)
    current_height = int(cfg.get("company_logo_height_px", 92) or 92)
    current_gap = int(cfg.get("company_logo_gap_px", 10) or 0)

    opacity_pct = st.slider(
        "Độ mờ logo (%)",
        min_value=0,
        max_value=100,
        value=max(0, min(100, current_opacity)),
        step=1,
        key="qlda_company_logo_opacity_v3",
        help="0% = ẩn hoàn toàn, 100% = hiển thị rõ hoàn toàn.",
    )

    c1, c2 = st.columns(2)
    height_px = c1.slider(
        "Chiều cao logo (px)",
        min_value=40,
        max_value=220,
        value=max(40, min(220, current_height)),
        step=2,
        key="qlda_company_logo_height_v3",
    )
    gap_px = c2.slider(
        "Khoảng cách logo → Đăng xuất (px)",
        min_value=0,
        max_value=40,
        value=max(0, min(40, current_gap)),
        step=1,
        key="qlda_company_logo_gap_v3",
    )

    st.caption(
        "Gợi ý: logo ngang dùng 80–110 px; logo vuông/cao dùng 90–140 px. "
        "Khoảng cách tới nút Đăng xuất thường 6–12 px."
    )

    save_col, delete_col = st.columns(2)

    if save_col.button(
        "💾 Lưu logo / hiển thị",
        type="primary",
        use_container_width=True,
        key="qlda_company_logo_save_v3",
    ):
        try:
            changed = [
                "company_logo_enabled",
                "company_logo_opacity_pct",
                "company_logo_height_px",
                "company_logo_gap_px",
            ]
            if upload is not None:
                save_company_logo(upload.getvalue())
                changed.append("company_logo_file")

            if current_logo_path() is None:
                raise ValueError("Hãy chọn file logo trước khi bật hiển thị.")

            ss.save_app_settings(
                {
                    "company_logo_enabled": True,
                    "company_logo_opacity_pct": int(opacity_pct),
                    "company_logo_height_px": int(height_px),
                    "company_logo_gap_px": int(gap_px),
                }
            )
            ss.append_settings_audit(actor, "update_company_logo", changed)
            st.success("Đã lưu logo. Logo sẽ nằm phía trên nút ‘Đăng xuất’ trên toàn app.")
            st.rerun()
        except Exception as exc:
            st.error(f"Không thể lưu logo: {exc}")

    if delete_col.button(
        "🗑️ Xóa logo",
        use_container_width=True,
        disabled=current is None,
        key="qlda_company_logo_delete_v3",
    ):
        try:
            delete_company_logo()
            ss.save_app_settings({"company_logo_enabled": False})
            ss.append_settings_audit(
                actor,
                "delete_company_logo",
                ["company_logo_enabled", "company_logo_file"],
            )
            st.success("Đã xóa logo. Khoảng logo phía trên Đăng xuất cũng được loại bỏ.")
            st.rerun()
        except Exception as exc:
            st.error(f"Không thể xóa logo: {exc}")


def install_company_branding_runtime(st: Any | None = None) -> None:
    """Install Admin settings integration and refresh branding every rerun."""
    if st is None:
        import streamlit as st  # type: ignore[no-redef]

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
        # Branding must never prevent the core application from starting.
        pass

    render_company_sidebar_logo(st)


__all__ = [
    "current_logo_path",
    "delete_company_logo",
    "install_company_branding_runtime",
    "render_company_logo_settings",
    "render_company_logo_watermark",
    "render_company_sidebar_logo",
    "save_company_logo",
]
