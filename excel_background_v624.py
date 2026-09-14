from __future__ import annotations

from typing import Any

from excel_jobs import build_upload_purpose, list_project_jobs

PATCH_VERSION = "V6.24 EXCEL BACKGROUND UI V1"


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    try:
        return row[key]
    except Exception:
        try:
            return dict(row).get(key, default)
        except Exception:
            return default


def _project_code(db, project_id: int) -> str:
    row = db.project(int(project_id))
    return str(_row_value(row, "code", "") or f"PROJECT-{int(project_id)}").strip()


def _status_label(status: str) -> str:
    return {
        "QUEUED": "🕓 Đang chờ",
        "RUNNING": "⚙️ Đang xử lý",
        "DONE": "✅ Hoàn thành",
        "FAILED": "❌ Lỗi",
        "CANCELLED": "⛔ Đã hủy",
    }.get(str(status or "").upper(), str(status or ""))


def render_boq_background_panel(
    st,
    db,
    project_id: int,
    *,
    gateway,
    session_token: str,
    can_update: bool,
    replace_existing_excel: bool = True,
) -> None:
    """Direct-to-disk BOQ upload + PostgreSQL background queue status."""
    if not can_update:
        return
    cfg = getattr(gateway, "config", None)
    if not bool(getattr(cfg, "local", False)):
        return
    token = str(session_token or "").strip()
    if not token:
        return

    st.markdown("##### ⚡ BOQ lớn · xử lý nền")
    c1, c2 = st.columns([2.2, 1.0])
    try:
        purpose = build_upload_purpose(
            "BOQ_IMPORT",
            int(project_id),
            replace_existing_excel=bool(replace_existing_excel),
        )
        upload = gateway.create_upload_ticket(
            token,
            project_code=_project_code(db, int(project_id)),
            kind="source",
            subtype="BOQ",
            record_code="BOQ",
            upload_purpose=purpose,
        )
        url = str(upload.get("url") or "").strip()
        max_gb = float(upload.get("max_gb") or 0)
        if url:
            label = "⬆️ Tải BOQ trực tiếp lên VPS"
            if max_gb:
                label += f" · tối đa {max_gb:g} GB"
            c1.link_button(label, url, use_container_width=True)
        else:
            c1.info("Chưa cấu hình Public Base URL nên chưa tạo được link upload trực tiếp.")
    except Exception as exc:
        c1.warning(f"Chưa tạo được phiên upload nền: {exc}")

    if c2.button("🔄 Làm mới", key=f"v624_boq_jobs_refresh_{int(project_id)}", use_container_width=True):
        st.rerun()

    try:
        jobs = list_project_jobs(int(project_id), job_type="BOQ_IMPORT", limit=5)
    except Exception as exc:
        st.warning(f"Chưa đọc được hàng đợi Excel: {exc}")
        return
    if not jobs:
        st.caption("File tải bằng nút trên được ghi thẳng xuống SSD VPS rồi worker xử lý riêng, không giữ workbook lớn trong RAM Streamlit.")
        return

    latest = jobs[0]
    status = str(latest.get("status") or "")
    progress = int(latest.get("progress") or 0)
    name = str(latest.get("source_name") or "BOQ.xlsx")
    step = str(latest.get("current_step") or "")
    st.write(f"{_status_label(status)} · **{name}** · Job #{latest.get('id')}")
    if status in {"QUEUED", "RUNNING"}:
        st.progress(max(0, min(100, progress)), text=f"{progress}% · {step}")
    elif status == "DONE":
        result = dict(latest.get("result") or {})
        st.success(
            f"Xử lý nền hoàn tất: {int(result.get('inserted') or 0):,} dòng BOQ"
            + (f" · {float(result.get('after_tax_total') or 0):,.0f} VND sau thuế" if result else "")
            + "."
        )
    elif status == "FAILED":
        st.error(f"Job BOQ thất bại: {latest.get('error_message') or step or 'Không rõ nguyên nhân'}")
