from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import settings_store as ss


PATCH_MARKER = "V6.22 ADMIN SYSTEM SETTINGS V1"
_ALLOWED_STORAGE_ROOT = Path(
    str(os.environ.get("QLDA_ADMIN_STORAGE_ALLOWED_ROOT", "/opt/qlda") or "/opt/qlda")
).expanduser()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _masked_secret(value: str) -> str:
    value = _text(value)
    if not value:
        return "Chưa cấu hình"
    if len(value) <= 8:
        return "••••••••"
    return f"{value[:4]}••••••••{value[-4:]}"


def _sanitize_database_url(value: str) -> str:
    raw = _text(value)
    if not raw:
        return "Chưa cấu hình"
    try:
        parsed = urlsplit(raw)
        if not parsed.scheme:
            return "Đã cấu hình"
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        user = parsed.username or ""
        auth = f"{user}:••••@" if user else ""
        netloc = f"{auth}{host}{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))
    except Exception:
        return "Đã cấu hình"


def _safe_storage_path(value: str, *, allow_equal_root: bool = True) -> Path:
    raw = _text(value)
    if not raw:
        raise ValueError("Đường dẫn lưu trữ không được để trống.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("Đường dẫn lưu trữ phải là đường dẫn tuyệt đối trên VPS.")
    resolved = path.resolve(strict=False)
    allowed = _ALLOWED_STORAGE_ROOT.resolve(strict=False)
    try:
        relative = resolved.relative_to(allowed)
    except Exception as exc:
        raise ValueError(f"Đường dẫn phải nằm trong {allowed} để phù hợp quyền systemd của dịch vụ QLDA.") from exc
    if not allow_equal_root and str(relative) == ".":
        raise ValueError(f"Không dùng trực tiếp thư mục gốc {allowed}; hãy chọn thư mục con.")
    return resolved


def _storage_test(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".qlda_write_test_", dir=str(path), delete=False) as handle:
            handle.write(b"QLDA V6.22 storage test")
            temp_name = handle.name
        Path(temp_name).unlink(missing_ok=True)
        usage = shutil.disk_usage(path)
        return True, f"Đọc/ghi OK · còn {usage.free / (1024 ** 3):.2f} GB trống."
    except Exception as exc:
        return False, f"Không thể đọc/ghi: {exc}"


def _test_database(db: Any) -> tuple[bool, str]:
    try:
        with db.connect() as connection:
            row = connection.execute("SELECT 1").fetchone()
        return (bool(row and int(row[0]) == 1), "PostgreSQL kết nối bình thường.")
    except Exception as exc:
        return False, f"Kết nối database lỗi: {exc}"


def _test_openai(api_key: str) -> tuple[bool, str]:
    key = _text(api_key)
    if not key:
        return False, "Chưa có OpenAI API key."
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, timeout=12.0)
        page = client.models.list()
        first = next(iter(page.data), None) if getattr(page, "data", None) else None
        label = getattr(first, "id", "") if first is not None else ""
        return True, f"OpenAI xác thực thành công{f' · model thấy được: {label}' if label else ''}."
    except Exception as exc:
        return False, f"OpenAI chưa kết nối được: {exc}"


def _test_gemini(api_key: str) -> tuple[bool, str]:
    key = _text(api_key)
    if not key:
        return False, "Chưa có Gemini API key."
    try:
        from google import genai

        client = genai.Client(api_key=key)
        pager = client.models.list()
        first = next(iter(pager), None)
        label = _text(getattr(first, "name", "")) if first is not None else ""
        return True, f"Gemini xác thực thành công{f' · model thấy được: {label}' if label else ''}."
    except Exception as exc:
        return False, f"Gemini chưa kết nối được: {exc}"


def _proc_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, tail = line.split(":", 1)
            number = int(tail.strip().split()[0]) * 1024
            values[key] = number
    except Exception:
        pass
    return values


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        ).stdout.strip()
        return out or "—"
    except Exception:
        return "—"


def _uptime_seconds() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
    except Exception:
        return None


def _format_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} ngày {hours} giờ"
    return f"{hours} giờ {minutes} phút"


def _render_ai(st: Any, actor: str) -> None:
    cfg = ss.load_app_settings()
    openai_runtime = ss.get_openai_runtime_settings()
    gemini_runtime = ss.get_gemini_runtime_settings()
    sources = ss.effective_settings_sources()

    st.markdown("#### 🤖 Kết nối AI")
    st.caption(
        "Admin quản lý provider/model/API key tại đây. API key được mã hóa trên VPS khi có "
        "QLDA_SETTINGS_MASTER_KEY hoặc QLDA_LOCAL_UPLOAD_SECRET."
    )

    c1, c2 = st.columns(2)
    c1.metric("Nguồn cấu hình", sources.get("ai", ""))
    c2.metric("Lưu secret", sources.get("secret_storage", ""))

    provider = st.selectbox(
        "AI mặc định",
        ["gemini", "openai"],
        index=0 if str(cfg.get("ai_provider")) == "gemini" else 1,
        format_func=lambda x: "Google Gemini" if x == "gemini" else "OpenAI",
        key="sys_ai_provider_v622",
    )
    col1, col2 = st.columns(2)
    gemini_model = col1.text_input(
        "Gemini model",
        value=_text(cfg.get("gemini_model")) or "auto",
        key="sys_gemini_model_v622",
    )
    openai_model = col2.text_input(
        "OpenAI model",
        value=_text(cfg.get("openai_model")) or "gpt-5-mini",
        key="sys_openai_model_v622",
    )

    st.caption(
        f"Gemini key hiện tại: {_masked_secret(gemini_runtime.get('api_key', ''))} · "
        f"OpenAI key hiện tại: {_masked_secret(openai_runtime.get('api_key', ''))}"
    )
    k1, k2 = st.columns(2)
    new_gemini_key = k1.text_input(
        "Gemini API key mới",
        value="",
        type="password",
        placeholder="Để trống = giữ key hiện tại",
        key="sys_gemini_key_v622",
    )
    new_openai_key = k2.text_input(
        "OpenAI API key mới",
        value="",
        type="password",
        placeholder="Để trống = giữ key hiện tại",
        key="sys_openai_key_v622",
    )
    use_web = st.toggle(
        "Cho phép AI dùng web search khi provider hỗ trợ",
        value=bool(cfg.get("openai_web_search", False)),
        key="sys_ai_web_v622",
    )

    t1, t2, save_col = st.columns([1, 1, 1.3])
    if t1.button("🧪 Test Gemini", key="sys_test_gemini_v622", use_container_width=True):
        ok, message = _test_gemini(new_gemini_key or gemini_runtime.get("api_key", ""))
        (st.success if ok else st.error)(message)
    if t2.button("🧪 Test OpenAI", key="sys_test_openai_v622", use_container_width=True):
        ok, message = _test_openai(new_openai_key or openai_runtime.get("api_key", ""))
        (st.success if ok else st.error)(message)
    if save_col.button("💾 Lưu cấu hình AI", type="primary", key="sys_save_ai_v622", use_container_width=True):
        # When switching from qlda.env to Admin-managed mode, blank password boxes
        # preserve the currently effective keys instead of accidentally disabling AI.
        updates = {
            "managed_ai": True,
            "ai_provider": provider,
            "gemini_model": _text(gemini_model) or "auto",
            "openai_model": _text(openai_model) or "gpt-5-mini",
            "gemini_api_key": _text(new_gemini_key) or _text(gemini_runtime.get("api_key")),
            "openai_api_key": _text(new_openai_key) or _text(openai_runtime.get("api_key")),
            "openai_web_search": bool(use_web),
        }
        if (updates["gemini_api_key"] or updates["openai_api_key"]) and not ss.encryption_available():
            st.error(
                "Chưa có khóa mã hóa runtime. Hãy giữ QLDA_LOCAL_UPLOAD_SECRET hoặc cấu hình "
                "QLDA_SETTINGS_MASTER_KEY trên VPS trước khi lưu API key từ giao diện."
            )
        else:
            ss.save_app_settings(updates)
            ss.append_settings_audit(actor, "update_ai", list(updates))
            st.success("Đã lưu cấu hình AI. Các request AI mới sử dụng cấu hình này ngay.")
            st.rerun()

    if bool(cfg.get("managed_ai")) and st.button(
        "↩️ Trả AI về qlda.env / Secrets",
        key="sys_reset_ai_v622",
    ):
        ss.save_app_settings({"managed_ai": False})
        ss.append_settings_audit(actor, "reset_ai_to_env", ["managed_ai"])
        st.success("Đã trả cấu hình AI về qlda.env / Streamlit Secrets.")
        st.rerun()


def _render_storage(st: Any, actor: str) -> None:
    cfg = ss.load_app_settings()
    sources = ss.effective_settings_sources()
    effective_root = ss.get_runtime_value("QLDA_LOCAL_STORAGE_ROOT", "/opt/qlda/data")
    effective_trash = ss.get_runtime_value("QLDA_LOCAL_TRASH_ROOT", f"{effective_root}/.trash")
    effective_public = ss.get_runtime_value("QLDA_PUBLIC_BASE_URL", "")

    st.markdown("#### 💾 Lưu trữ VPS")
    st.caption(
        "Thay đổi đường dẫn chỉ áp dụng trong /opt/qlda để giữ đúng quyền systemd hiện tại. "
        "Không xóa hoặc di chuyển file cũ tự động."
    )
    st.metric("Nguồn cấu hình", sources.get("storage", ""))

    root = st.text_input("Thư mục dữ liệu", value=effective_root, key="sys_storage_root_v622")
    trash = st.text_input("Thư mục thùng rác", value=effective_trash, key="sys_trash_root_v622")
    public_url = st.text_input("Public base URL", value=effective_public, key="sys_public_url_v622")

    c1, c2, c3 = st.columns(3)
    direct_mb = c1.number_input(
        "Upload trực tiếp tối đa (MB)",
        min_value=1,
        max_value=4096,
        value=int(float(ss.get_runtime_value("QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB", "2048") or 2048)),
        step=50,
        key="sys_direct_mb_v622",
    )
    legacy_mb = c2.number_input(
        "Upload legacy tối đa (MB)",
        min_value=1,
        max_value=1024,
        value=int(float(ss.get_runtime_value("QLDA_LOCAL_LEGACY_MAX_UPLOAD_MB", "200") or 200)),
        step=10,
        key="sys_legacy_mb_v622",
    )
    ttl_hours = c3.number_input(
        "Phiên upload (giờ)",
        min_value=1,
        max_value=720,
        value=int(float(ss.get_runtime_value("QLDA_LOCAL_SESSION_TTL_HOURS", "12") or 12)),
        step=1,
        key="sys_ttl_v622",
    )

    b1, b2 = st.columns([1, 1.4])
    if b1.button("🧪 Test đọc/ghi", key="sys_storage_test_v622", use_container_width=True):
        try:
            tested = _safe_storage_path(root, allow_equal_root=False)
            ok, message = _storage_test(tested)
            (st.success if ok else st.error)(message)
        except Exception as exc:
            st.error(str(exc))

    if b2.button("💾 Lưu cấu hình lưu trữ", type="primary", key="sys_storage_save_v622", use_container_width=True):
        try:
            root_path = _safe_storage_path(root, allow_equal_root=False)
            trash_path = _safe_storage_path(trash, allow_equal_root=False)
            try:
                trash_path.relative_to(root_path)
            except Exception as exc:
                raise ValueError("Thư mục thùng rác phải nằm bên trong thư mục dữ liệu.") from exc
            ok, message = _storage_test(root_path)
            if not ok:
                raise ValueError(message)
            updates = {
                "managed_storage": True,
                "local_storage_root": str(root_path),
                "local_trash_root": str(trash_path),
                "public_base_url": _text(public_url).rstrip("/"),
                "local_direct_max_upload_mb": int(direct_mb),
                "local_legacy_max_upload_mb": int(legacy_mb),
                "local_session_ttl_hours": int(ttl_hours),
            }
            ss.save_app_settings(updates)
            ss.append_settings_audit(actor, "update_storage", list(updates))
            st.success("Đã lưu cấu hình lưu trữ. Request/upload mới sẽ đọc cấu hình mới.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    if bool(cfg.get("managed_storage")) and st.button(
        "↩️ Trả lưu trữ về qlda.env",
        key="sys_reset_storage_v622",
    ):
        ss.save_app_settings({"managed_storage": False})
        ss.append_settings_audit(actor, "reset_storage_to_env", ["managed_storage"])
        st.success("Đã trả cấu hình lưu trữ về qlda.env.")
        st.rerun()


def _render_performance(st: Any, actor: str) -> None:
    cfg = ss.load_app_settings()
    cpu_total = max(1, int(os.cpu_count() or 1))
    max_workers = max(1, min(8, cpu_total - 1 if cpu_total > 1 else 1))

    st.markdown("#### ⚙️ Hiệu năng ứng dụng")
    st.caption(
        "Đây là giới hạn của app, không thay đổi số CPU/RAM vật lý của VPS. "
        "Với VPS 4 vCPU hiện tại nên giữ 3 Excel child workers."
    )
    st.metric("Nguồn cấu hình", ss.effective_settings_sources().get("performance", ""))

    c1, c2, c3 = st.columns(3)
    workers_current = int(float(ss.get_runtime_value("QLDA_CPU_WORKERS", str(min(3, max_workers))) or min(3, max_workers)))
    workers = c1.number_input(
        "Excel child workers",
        min_value=1,
        max_value=max_workers,
        value=max(1, min(max_workers, workers_current)),
        step=1,
        key="sys_workers_v622",
    )
    min_mb = c2.number_input(
        "File tối thiểu chạy multicore (MB)",
        min_value=0.0,
        max_value=512.0,
        value=float(ss.get_runtime_value("QLDA_PARALLEL_EXCEL_MIN_MB", "2") or 2),
        step=0.5,
        key="sys_parallel_mb_v622",
    )
    min_sheets = c3.number_input(
        "Số sheet tối thiểu chạy multicore",
        min_value=1,
        max_value=100,
        value=int(float(ss.get_runtime_value("QLDA_PARALLEL_MIN_SHEETS", "2") or 2)),
        step=1,
        key="sys_parallel_sheets_v622",
    )
    available_methods = [x for x in ("forkserver", "spawn", "fork") if x]
    current_method = ss.get_runtime_value("QLDA_MP_START_METHOD", "forkserver")
    method = st.selectbox(
        "Multiprocessing start method",
        available_methods,
        index=available_methods.index(current_method) if current_method in available_methods else 0,
        key="sys_mp_method_v622",
    )

    if st.button("💾 Lưu giới hạn hiệu năng", type="primary", key="sys_perf_save_v622"):
        updates = {
            "managed_performance": True,
            "cpu_workers": int(workers),
            "parallel_excel_min_mb": float(min_mb),
            "parallel_min_sheets": int(min_sheets),
            "mp_start_method": method,
        }
        ss.save_app_settings(updates)
        ss.append_settings_audit(actor, "update_performance", list(updates))
        st.success("Đã lưu cấu hình hiệu năng. Các tác vụ Excel mới sẽ sử dụng giá trị mới.")
        st.rerun()

    if bool(cfg.get("managed_performance")) and st.button(
        "↩️ Trả hiệu năng về qlda.env",
        key="sys_reset_perf_v622",
    ):
        ss.save_app_settings({"managed_performance": False})
        ss.append_settings_audit(actor, "reset_performance_to_env", ["managed_performance"])
        st.success("Đã trả cấu hình hiệu năng về qlda.env.")
        st.rerun()


def _render_vps(st: Any, db: Any) -> None:
    st.markdown("#### 🖥️ VPS / Database")
    mem = _proc_meminfo()
    total = int(mem.get("MemTotal", 0))
    available = int(mem.get("MemAvailable", 0))
    used = max(0, total - available)
    cpu = max(1, int(os.cpu_count() or 1))
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    root = Path(ss.get_runtime_value("QLDA_LOCAL_STORAGE_ROOT", "/opt/qlda/data"))
    try:
        disk = shutil.disk_usage(root if root.exists() else Path("/"))
    except Exception:
        disk = shutil.disk_usage("/")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("CPU", f"{cpu} vCPU")
    c2.metric("RAM", f"{total / (1024 ** 3):.2f} GB" if total else "—")
    c3.metric("RAM đang dùng", f"{used / (1024 ** 3):.2f} GB" if total else "—")
    c4.metric("Disk còn trống", f"{disk.free / (1024 ** 3):.2f} GB")

    st.caption(
        f"Host: {socket.gethostname()} · Load 1/5/15 phút: {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f} · "
        f"Uptime: {_format_uptime(_uptime_seconds())}"
    )

    r1, r2, r3 = st.columns(3)
    r1.code(f"Python {platform.python_version()}", language=None)
    r2.code(f"App commit {_git_commit()}", language=None)
    r3.code(f"Platform {platform.system()} {platform.release()}", language=None)

    db_url = (
        _text(os.environ.get("DATABASE_URL"))
        or _text(os.environ.get("QLDA_DATABASE_URL"))
        or _text(os.environ.get("POSTGRES_URL"))
    )
    st.text_input("Database (đã che mật khẩu)", value=_sanitize_database_url(db_url), disabled=True)
    if st.button("🧪 Test database", key="sys_db_test_v622"):
        ok, message = _test_database(db)
        (st.success if ok else st.error)(message)

    st.info(
        "CPU, RAM, dung lượng đĩa, DATABASE_URL, port dịch vụ, firewall, SSH và Linux service user là "
        "thông số hạ tầng. App chỉ hiển thị/kiểm tra; không cho sửa trực tiếp để tránh tự làm mất kết nối VPS."
    )


def _render_audit(st: Any) -> None:
    st.markdown("#### 🧾 Nhật ký thay đổi cài đặt")
    rows = ss.read_settings_audit(150)
    if not rows:
        st.info("Chưa có thay đổi cài đặt được ghi nhận từ giao diện Admin.")
        return
    table = []
    for row in rows:
        table.append(
            {
                "UTC": row.get("time_utc", ""),
                "Admin": row.get("actor", ""),
                "Thao tác": row.get("action", ""),
                "Trường thay đổi": ", ".join(row.get("changed_keys") or []),
            }
        )
    st.dataframe(table, use_container_width=True, hide_index=True)
    st.caption("Nhật ký chỉ lưu tên trường thay đổi, không bao giờ lưu API key hoặc mật khẩu.")


def render_system_settings_admin(
    st: Any,
    db: Any,
    *,
    is_admin: bool = False,
    actor: str = "",
) -> None:
    """Admin-only system console for AI, storage, app limits and VPS status."""
    if not bool(is_admin):
        st.error("🔒 Cài đặt hệ thống chỉ dành cho Admin.")
        return

    st.subheader("🛠️ Cài đặt hệ thống · Admin")
    st.caption(
        "V6.22 · Các thay đổi ở đây không sửa dữ liệu nghiệp vụ. Bootstrap secrets như DATABASE_URL, "
        "upload signing secret và SSH vẫn được giữ ngoài app."
    )

    tab_ai, tab_storage, tab_perf, tab_vps, tab_audit = st.tabs(
        ["🤖 AI", "💾 Lưu trữ", "⚙️ Hiệu năng", "🖥️ VPS", "🧾 Nhật ký"]
    )
    with tab_ai:
        _render_ai(st, actor)
    with tab_storage:
        _render_storage(st, actor)
    with tab_perf:
        _render_performance(st, actor)
    with tab_vps:
        _render_vps(st, db)
    with tab_audit:
        _render_audit(st)
