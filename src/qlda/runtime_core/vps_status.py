from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PATCH_MARKER = "V6.22 VPS STORAGE STATUS V1 ADMIN ONLY"
CACHE_KEY = "_qlda_vps_storage_status_v622"
CACHE_TTL_SECONDS = 60
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def human_bytes(value: Any) -> str:
    try:
        size = float(value or 0)
    except Exception:
        return "—"
    if size < 0:
        return "—"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size):,} {unit}"
            return f"{size:,.2f} {unit}"
        size /= 1024.0
    return "—"


def disk_health(used_percent: float) -> tuple[str, str]:
    pct = max(0.0, float(used_percent or 0.0))
    if pct < 70.0:
        return "success", "🟢 Bình thường"
    if pct < 85.0:
        return "warning", "🟡 Cần theo dõi"
    if pct < 95.0:
        return "warning", "🟠 Sắp đầy"
    return "error", "🔴 Nguy hiểm"


def _run_text(args: list[str], timeout: float = 5.0) -> str:
    completed = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return str(completed.stdout or "").strip()


def _nearest_existing_path(path: Path) -> Path:
    current = path.expanduser()
    while not current.exists() and current != current.parent:
        current = current.parent
    return current if current.exists() else Path("/")


def _filesystem_usage(path: Path) -> dict[str, Any]:
    target = _nearest_existing_path(path)
    try:
        output = _run_text(["df", "-P", "-B1", str(target)], timeout=4.0)
        lines = [line for line in output.splitlines() if line.strip()]
        fields = lines[-1].split()
        if len(fields) >= 6:
            filesystem = fields[0]
            total = int(fields[1])
            used = int(fields[2])
            free = int(fields[3])
            mount_point = fields[-1]
            used_percent = (used / total * 100.0) if total else 0.0
            return {
                "filesystem": filesystem,
                "mount_point": mount_point,
                "target": str(target),
                "total": total,
                "used": used,
                "free": free,
                "used_percent": used_percent,
            }
    except Exception:
        pass

    usage = shutil.disk_usage(target)
    total = int(usage.total)
    used = int(usage.used)
    free = int(usage.free)
    return {
        "filesystem": "",
        "mount_point": str(target),
        "target": str(target),
        "total": total,
        "used": used,
        "free": free,
        "used_percent": (used / total * 100.0) if total else 0.0,
    }


def _directory_size(path: Path) -> tuple[int | None, str]:
    path = path.expanduser()
    if not path.exists():
        return None, "Chưa tồn tại"
    try:
        output = _run_text(["du", "-sb", str(path)], timeout=8.0)
        value = int(output.split()[0])
        return value, ""
    except subprocess.TimeoutExpired:
        return None, "Quá thời gian đọc"
    except PermissionError:
        return None, "Không đủ quyền đọc"
    except Exception:
        return None, "Không đọc được"


def _postgres_database_size(db: Any) -> tuple[int | None, str]:
    if db is None or not hasattr(db, "connect"):
        return None, "Không có kết nối DB"
    try:
        with db.connect() as connection:
            row = connection.execute(
                "SELECT pg_database_size(current_database()) AS database_size"
            ).fetchone()
        if row is None:
            return None, "Không đọc được"
        value = row[0]
        return int(value), ""
    except Exception:
        # SQLite/dev mode or a restricted PostgreSQL role should not break the UI.
        return None, "Không đọc được bằng kết nối hiện tại"


def collect_vps_storage(
    db: Any = None,
    *,
    data_path: str | Path | None = None,
    app_path: str | Path | None = None,
    shared_path: str | Path | None = None,
    log_path: str | Path | None = None,
    disk_path: str | Path | None = None,
) -> dict[str, Any]:
    data_root = Path(
        data_path
        or os.environ.get("QLDA_LOCAL_STORAGE_ROOT")
        or "/opt/qlda/data"
    )
    app_root = Path(app_path or os.environ.get("QLDA_APP_DIR") or "/opt/qlda/app")
    shared_root = Path(shared_path or os.environ.get("QLDA_SHARED_DIR") or "/opt/qlda/shared")
    logs_root = Path(log_path or "/var/log")

    if disk_path is not None:
        disk_target = Path(disk_path)
    elif data_root.exists():
        disk_target = data_root
    elif app_root.exists():
        disk_target = app_root
    else:
        disk_target = Path("/")

    disk = _filesystem_usage(disk_target)
    status_kind, status_label = disk_health(float(disk.get("used_percent") or 0.0))

    components: list[dict[str, Any]] = []
    for label, path in (
        ("File dữ liệu QLDA", data_root),
        ("Mã nguồn ứng dụng", app_root),
        ("Dữ liệu dùng chung", shared_root),
        ("Log hệ thống", logs_root),
    ):
        size, note = _directory_size(path)
        components.append(
            {
                "component": label,
                "location": str(path),
                "bytes": size,
                "note": note,
            }
        )

    pg_size, pg_note = _postgres_database_size(db)
    components.insert(
        3,
        {
            "component": "PostgreSQL hiện tại",
            "location": "pg_database_size(current_database())",
            "bytes": pg_size,
            "note": pg_note,
        },
    )

    return {
        "marker": PATCH_MARKER,
        "host": socket.gethostname(),
        "storage_backend": str(os.environ.get("QLDA_STORAGE_BACKEND", "") or "").strip() or "Không xác định",
        "updated_at": datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M:%S"),
        "disk": disk,
        "health_kind": status_kind,
        "health_label": status_label,
        "components": components,
    }


def _get_cached_payload(st: Any, db: Any, refresh: bool) -> dict[str, Any]:
    now = time.time()
    cached = st.session_state.get(CACHE_KEY)
    if (
        not refresh
        and isinstance(cached, dict)
        and isinstance(cached.get("payload"), dict)
        and now - float(cached.get("time") or 0.0) < CACHE_TTL_SECONDS
    ):
        return dict(cached["payload"])

    payload = collect_vps_storage(db)
    st.session_state[CACHE_KEY] = {"time": now, "payload": payload}
    return payload


def render_vps_status_v622(st: Any, db: Any = None, *, is_admin: bool = False) -> None:
    """Render live VPS storage metrics. The page is intentionally Admin-only."""
    if not bool(is_admin):
        st.warning("🔒 Trạng thái VPS chỉ dành cho Admin.")
        return

    st.subheader("🖥️ Trạng thái VPS")
    refresh = st.button(
        "🔄 Làm mới thông số VPS",
        key="qlda_vps_status_refresh_v622",
        use_container_width=False,
    )
    with st.spinner("Đang đọc dung lượng VPS..."):
        payload = _get_cached_payload(st, db, refresh)

    disk = dict(payload.get("disk") or {})
    total = int(disk.get("total") or 0)
    used = int(disk.get("used") or 0)
    free = int(disk.get("free") or 0)
    used_percent = float(disk.get("used_percent") or 0.0)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tổng ổ đĩa", human_bytes(total))
    m2.metric("Đã sử dụng", human_bytes(used))
    m3.metric("Còn trống", human_bytes(free))
    m4.metric("Mức sử dụng", f"{used_percent:.1f}%")

    st.progress(
        min(1.0, max(0.0, used_percent / 100.0)),
        text=f"{payload.get('health_label', '')} · còn {human_bytes(free)}",
    )

    message = (
        f"Ổ đĩa {disk.get('filesystem') or 'hệ thống'} · "
        f"mount {disk.get('mount_point') or '/'} · "
        f"đang dùng {used_percent:.1f}%"
    )
    health_kind = str(payload.get("health_kind") or "success")
    if health_kind == "error":
        st.error(f"{payload.get('health_label', '')} — {message}")
    elif health_kind == "warning":
        st.warning(f"{payload.get('health_label', '')} — {message}")
    else:
        st.success(f"{payload.get('health_label', '')} — {message}")

    st.markdown("#### Dung lượng theo thành phần")
    rows = []
    for item in payload.get("components") or []:
        size = item.get("bytes")
        rows.append(
            {
                "Thành phần": item.get("component", ""),
                "Vị trí": item.get("location", ""),
                "Dung lượng": human_bytes(size) if size is not None else "—",
                "Trạng thái": item.get("note") or "OK",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.caption(
        f"Máy chủ: {payload.get('host', '')} · "
        f"Storage: {payload.get('storage_backend', '')} · "
        f"Cập nhật: {payload.get('updated_at', '')} (UTC+7). "
        "Thông số được đọc trực tiếp từ VPS; dữ liệu được giữ tối đa 60 giây giữa các lần tải trang."
    )
