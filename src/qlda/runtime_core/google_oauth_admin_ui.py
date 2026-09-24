from __future__ import annotations

from typing import Any

import qlda.runtime_core.google_oauth_settings as gos
import qlda.runtime_core.settings_store as ss


def _mask(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "Chưa cấu hình"
    if len(text) <= 10:
        return "••••••••"
    return f"{text[:6]}••••••••{text[-4:]}"


def render_google_oauth_settings(st: Any, actor: str = "") -> None:
    cfg = gos.get_google_oauth_settings()
    current_id = str(cfg.get("client_id") or "").strip()
    current_secret = str(cfg.get("client_secret") or "").strip()
    current_redirect = str(cfg.get("redirect_uri") or gos.DEFAULT_REDIRECT_URI).strip()

    st.markdown("#### 🔐 Google OAuth · Google Sheets")
    st.caption(
        "Cấu hình này dùng cho Google Sheet riêng tư trong mục Thi công → Sản lượng. "
        "Admin nhập trực tiếp tại đây; QLDA không cần sửa qlda.env. Client Secret được mã hóa trên VPS."
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Nguồn cấu hình", str(cfg.get("source") or "Chưa cấu hình"))
    c2.metric("Client ID", _mask(current_id))
    c3.metric("Client Secret", "Đã cấu hình" if current_secret else "Chưa cấu hình")

    if not gos.encryption_available():
        st.error(
            "VPS chưa có khóa mã hóa runtime. QLDA cần QLDA_LOCAL_UPLOAD_SECRET hoặc "
            "QLDA_SETTINGS_MASTER_KEY để lưu Client Secret an toàn."
        )

    client_id = st.text_input(
        "Google OAuth Client ID",
        value=current_id,
        placeholder="xxxxxxxxxxxx-xxxxxxxx.apps.googleusercontent.com",
        key="sys_google_oauth_client_id",
    )
    client_secret_new = st.text_input(
        "Google OAuth Client Secret",
        value="",
        type="password",
        placeholder="Để trống = giữ Client Secret hiện tại",
        key="sys_google_oauth_client_secret",
    )
    redirect_uri = st.text_input(
        "Authorized Redirect URI",
        value=current_redirect or gos.DEFAULT_REDIRECT_URI,
        placeholder="https://qldaxd.id.vn",
        key="sys_google_oauth_redirect_uri",
    )

    st.info(
        "Trong Google Cloud Console, Authorized redirect URI phải khớp chính xác với giá trị trên. "
        "Với hệ thống hiện tại nên dùng https://qldaxd.id.vn"
    )

    test_col, save_col = st.columns([1, 1.4])
    effective_secret = str(client_secret_new or current_secret).strip()

    if test_col.button("🧪 Kiểm tra cấu hình", key="sys_google_oauth_test", use_container_width=True):
        ok, message = gos.validate_google_oauth(client_id, effective_secret, redirect_uri)
        (st.success if ok else st.error)(message)

    if save_col.button(
        "💾 Lưu Google OAuth",
        type="primary",
        key="sys_google_oauth_save",
        use_container_width=True,
    ):
        try:
            path = gos.save_google_oauth_settings(client_id, effective_secret, redirect_uri)
            ss.append_settings_audit(
                actor,
                "update_google_oauth",
                ["google_oauth_client_id", "google_oauth_client_secret", "google_oauth_redirect_uri"],
            )
            st.success(
                f"Đã lưu và áp dụng Google OAuth. Client Secret được mã hóa tại {path}. "
                "Có thể quay lại Sản lượng và bấm Đăng nhập Google ngay."
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    if bool(cfg.get("managed")):
        if st.button("↩️ Xóa cấu hình trong app và dùng fallback hệ thống", key="sys_google_oauth_reset"):
            gos.delete_managed_google_oauth_settings()
            ss.append_settings_audit(actor, "reset_google_oauth", ["google_oauth"])
            st.success("Đã xóa cấu hình Google OAuth do Admin lưu trong app.")
            st.rerun()

    with st.expander("Hướng dẫn tạo OAuth Client trên Google Cloud", expanded=False):
        st.markdown(
            """
1. Mở **Google Cloud Console** và chọn/tạo một project cho QLDA.
2. Bật **Google Sheets API**.
3. Vào **Google Auth Platform / OAuth consent screen** và cấu hình ứng dụng.
4. Tạo **OAuth Client ID** loại **Web application**.
5. Trong **Authorized redirect URIs**, thêm đúng URI đang hiển thị ở trên.
6. Sao chép **Client ID** và **Client Secret** vào màn hình này rồi bấm **Lưu Google OAuth**.
7. Quay lại **Thi công → Sản lượng → Google Sheets → Đăng nhập Google**.
            """
        )
