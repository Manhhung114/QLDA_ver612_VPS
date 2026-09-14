from __future__ import annotations

from typing import Any

from excel_jobs_v624 import build_upload_purpose, list_jobs, request_cancel

PATCH_VERSION = "V6.24.2 EXCEL BACKGROUND UI"


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
    """Direct-to-SSD BOQ upload + PostgreSQL worker progress for one workspace."""
    cfg = getattr(gateway, "config", None)
    if not bool(getattr(cfg, "local", False)):
        return
    token = str(session_token or "").strip()
    if not token:
        return

    pid = int(project_id)
    st.markdown("##### ⚡ BOQ lớn · xử lý nền V6.24.2")
    st.caption(
        "File được tải thẳng xuống SSD VPS. Worker riêng đọc Excel ở chế độ read-only, "
        "ghi BOQ theo lô vào PostgreSQL và không giữ workbook lớn trong RAM Streamlit."
    )

    c1, c2 = st.columns([2.2, 1.0])
    if can_update:
        try:
            purpose = build_upload_purpose(
                "BOQ",
                pid,
                workspace_project_id=pid,
                replace_existing_excel=bool(replace_existing_excel),
            )
            upload = gateway.create_upload_ticket(
                token,
                project_code=_project_code(db, pid),
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
    else:
        c1.caption("Tài khoản hiện tại chỉ có quyền xem trạng thái xử lý BOQ.")

    if c2.button("🔄 Làm mới", key=f"v624_boq_jobs_refresh_{pid}", use_container_width=True):
        st.rerun()

    try:
        jobs = list_jobs(pid, job_type="BOQ", limit=5)
    except Exception as exc:
        st.warning(f"Chưa đọc được hàng đợi Excel: {exc}")
        return
    if not jobs:
        st.caption("Chưa có tác vụ BOQ nền cho nhà thầu/dự án đang chọn.")
        return

    latest = jobs[0]
    status = str(latest.get("status") or "").upper()
    progress = int(latest.get("progress") or 0)
    name = str(latest.get("file_name") or latest.get("source_name") or "BOQ.xlsx")
    stage = str(latest.get("stage") or latest.get("current_step") or "")
    current_sheet = str(latest.get("current_sheet") or "")
    job_id = int(latest.get("id") or 0)

    st.write(f"{_status_label(status)} · **{name}** · Job #{job_id}")
    if status in {"QUEUED", "RUNNING"}:
        detail = stage + (f" · Sheet: {current_sheet}" if current_sheet else "")
        st.progress(max(0, min(100, progress)), text=f"{progress}% · {detail}")
        if can_update and job_id > 0:
            if st.button(
                "⛔ Yêu cầu hủy tác vụ",
                key=f"v624_boq_cancel_{pid}_{job_id}",
                disabled=status not in {"QUEUED", "RUNNING"},
            ):
                try:
                    request_cancel(job_id)
                    st.warning("Đã gửi yêu cầu hủy. Worker sẽ dừng tại điểm kiểm tra an toàn gần nhất.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không thể hủy tác vụ: {exc}")
    elif status == "DONE":
        result = dict(latest.get("result") or {})
        st.success(
            f"Xử lý nền hoàn tất: {int(result.get('inserted') or 0):,} dòng BOQ"
            + (f" · {float(result.get('after_tax_total') or 0):,.0f} VND sau thuế" if result else "")
            + "."
        )
    elif status == "FAILED":
        st.error(f"Job BOQ thất bại: {latest.get('error_message') or stage or 'Không rõ nguyên nhân'}")
    elif status == "CANCELLED":
        st.info("Tác vụ BOQ đã được hủy.")

    if len(jobs) > 1:
        with st.expander("Lịch sử 5 tác vụ BOQ gần nhất", expanded=False):
            for row in jobs:
                row_status = str(row.get("status") or "").upper()
                row_name = str(row.get("file_name") or row.get("source_name") or "BOQ.xlsx")
                row_stage = str(row.get("stage") or row.get("current_step") or "")
                st.caption(
                    f"{_status_label(row_status)} · Job #{row.get('id')} · {row_name}"
                    + (f" · {row_stage}" if row_stage else "")
                )
