from __future__ import annotations

"""A single entry point for importing Excel and Microsoft Project schedules.

Preview never writes to the database. A confirmed import updates only the
source family being imported: Excel -> schedule_excel:*, Project -> mpp.
"""

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from qlda.import_engines.schedule_background import parse_schedule_excel_path
from qlda.import_engines.schedule_persistence import save_schedule_result_batched
from qlda.infrastructure.schedule_workspace import delete_schedule_scope, source_counts
from qlda.runtime_core.mpp_cloud_reader import read_mpp

UploadArchive = Callable[[str, bytes, str, str], Any]
_MAX_DIRECT_UPLOAD_BYTES = 80 * 1024 * 1024
_ALLOWED_TYPES = {
    ".xlsx": "excel", ".xlsm": "excel",
    ".mpp": "project", ".xml": "project",
}


def classify_schedule_file(filename: str) -> str:
    """Explicitly route each supported extension to exactly one importer."""
    kind = _ALLOWED_TYPES.get(Path(str(filename or "")).suffix.lower())
    if not kind:
        raise ValueError("Chỉ nhận Excel (.xlsx/.xlsm) hoặc Microsoft Project (.mpp/.xml).")
    return kind


def _read_uploaded_schedule(uploaded: Any, *, status_date: Any) -> dict[str, Any]:
    kind = classify_schedule_file(uploaded.name)
    data = uploaded.getvalue()
    if not data:
        raise ValueError("File tải lên đang trống.")
    if len(data) > _MAX_DIRECT_UPLOAD_BYTES:
        raise ValueError("File vượt 80 MB. Hãy dùng nhập Excel lớn trực tiếp trên VPS.")
    digest = hashlib.sha256(data).hexdigest()
    source = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=Path(uploaded.name).suffix.lower(), delete=False
        ) as handle:
            handle.write(data)
            source = Path(handle.name)
        if kind == "excel":
            result = parse_schedule_excel_path(
                source, filename=uploaded.name, status_date=status_date,
            )
            tasks = result["tasks"]
            details = {
                "kind": kind, "filename": uploaded.name, "sha256": digest,
                "tasks": tasks, "result": result,
                "project_info": {}, "task_count": len(tasks),
                "dated_count": int(result.get("dated_count") or 0),
                "undated_count": int(result.get("undated_count") or 0),
            }
        else:
            info, tasks = read_mpp(source, status_date=status_date)
            if not tasks:
                raise ValueError("File Microsoft Project không có công việc hợp lệ.")
            details = {
                "kind": kind, "filename": uploaded.name, "sha256": digest,
                "tasks": tasks, "project_info": info, "result": {},
                "task_count": len(tasks),
                "dated_count": sum(bool(t.get("start_date") and t.get("end_date")) for t in tasks),
                "undated_count": sum(not(t.get("start_date") and t.get("end_date")) for t in tasks),
            }
        return details
    finally:
        if source is not None:
            source.unlink(missing_ok=True)


def render_schedule_import(
    st, db, project_id: int, *,
    status_date: Any,
    can_update: bool,
    archive_original: UploadArchive | None = None,
) -> None:
    """Render the single .xlsx/.xlsm/.mpp/.xml uploader and import confirmation."""
    pid = int(project_id)
    state_key = f"schedule_unified_upload_preview_{pid}"
    accept_key = f"schedule_unified_upload_accept_{pid}"

    st.markdown("#### 📁 Tải file tiến độ Excel / Microsoft Project")
    st.caption(
        "Một vị trí tải file dùng chung: Excel (.xlsx, .xlsm) hoặc Microsoft Project (.mpp, .xml). "
        "Ứng dụng tự nhận loại file, hiển thị công việc và ngày để kiểm tra trước khi nhập. "
        "Nhập mới chỉ cập nhật các công việc cùng loại nguồn, không xóa nguồn còn lại."
    )
    uploaded = st.file_uploader(
        "Chọn file tiến độ", type=["xlsx", "xlsm", "mpp", "xml"],
        key=f"schedule_unified_file_{pid}",
        disabled=not can_update,
    )
    if uploaded is None:
        st.session_state.pop(state_key, None)
        return

    try:
        kind = classify_schedule_file(uploaded.name)
    except ValueError as exc:
        st.error(str(exc))
        return
    file_size = int(getattr(uploaded, "size", 0) or 0)
    if file_size > _MAX_DIRECT_UPLOAD_BYTES:
        st.error("File vượt giới hạn nhập trực tiếp 80 MB.")
        return

    digest = hashlib.sha256(uploaded.getvalue()).hexdigest()
    current = st.session_state.get(state_key)
    if current is not None and (
        current.get("sha256") != digest or current.get("filename") != uploaded.name
    ):
        st.session_state.pop(state_key, None)
        current = None

    kind_label = "Excel" if kind == "excel" else "Microsoft Project"
    st.caption(f"Đã chọn: {uploaded.name} · Nhận diện: {kind_label}.")
    if st.button(
        "🔎 Đọc và xem trước tiến độ",
        key=f"schedule_unified_preview_{pid}",
        disabled=not can_update,
        type="primary",
    ):
        try:
            with st.spinner(f"Đang đọc file {kind_label}, chưa lưu dữ liệu..."):
                current = _read_uploaded_schedule(uploaded, status_date=status_date)
            st.session_state[state_key] = current
            st.session_state[accept_key] = False
        except Exception as exc:
            st.session_state.pop(state_key, None)
            st.error(f"Không đọc được tiến độ: {exc}")
            return

    if not current:
        st.info("Nhấn 'Đọc và xem trước tiến độ' để kiểm tra ngày và công việc trước khi nhập.")
        return

    if current["kind"] != kind:
        st.error("Loại file đã thay đổi. Vui lòng đọc lại.")
        return

    tasks = current["tasks"]
    left, middle, right = st.columns(3)
    left.metric("Công việc", int(current["task_count"]))
    middle.metric("Có ngày", int(current["dated_count"]))
    right.metric("Chưa có ngày", int(current["undated_count"]))

    rows = [{
        "Dòng/ID gốc": task.get("source_row_no") if kind == "excel" else task.get("task_id"),
        "WBS": task.get("wbs", ""),
        "Hạng mục": task.get("name", ""),
        "Bắt đầu": task.get("start_date", ""),
        "Kết thúc": task.get("end_date", ""),
        "Số ngày": task.get("duration", 0),
    } for task in tasks[:200]]
    st.dataframe(
        pd.DataFrame(rows), hide_index=True, use_container_width=True,
        height=min(480, 95 + 31 * min(12, len(rows))),
    )
    if len(tasks) > len(rows):
        st.caption(f"Đang xem {len(rows)}/{len(tasks)} công việc đầu tiên; khi xác nhận sẽ nhập đủ.")
    if kind == "excel":
        info = current["result"]
        st.caption(
            f"Sheet: {info.get('sheet_name', '')} · Dòng tiêu đề: {info.get('header_row', 1)}. "
            "Giữ ngày và Số ngày gốc, không dịch lịch."
        )
        for message in info.get("warnings") or []:
            st.warning(message)
        if info.get("skipped_details"):
            with st.expander("Dòng Excel không hợp lệ"):
                st.dataframe(
                    pd.DataFrame(info["skipped_details"]),
                    hide_index=True, use_container_width=True,
                )
        if info.get("summary_date_conflicts"):
            with st.expander("Mốc tổng hợp cần kiểm tra"):
                st.dataframe(
                    pd.DataFrame(info["summary_date_conflicts"]),
                    hide_index=True, use_container_width=True,
                )
    else:
        st.caption("Ngày bắt đầu/kết thúc lấy trực tiếp từ dữ liệu Project đọc bằng MPXJ.")

    st.checkbox(
        f"Tôi đã xem ngày và đồng ý nhập {len(tasks):,} công việc {kind_label}.",
        key=accept_key, disabled=not can_update,
    )
    if not st.button(
        f"✅ Nhập tiến độ {kind_label} vào bảng",
        key=f"schedule_unified_commit_{pid}",
        disabled=not(can_update and st.session_state.get(accept_key)),
    ):
        return

    if hashlib.sha256(uploaded.getvalue()).hexdigest() != current["sha256"]:
        st.error("File vừa thay đổi, cần đọc và xác nhận lại.")
        st.session_state.pop(state_key, None)
        return
    try:
        if kind == "excel":
            stats = save_schedule_result_batched(db, pid, current["result"])
            written = int(stats["written_rows"])
        else:
            db.sync_mpp_tasks(pid, current["tasks"], uploaded.name, current["project_info"])
            written = len(current["tasks"])
    except Exception as exc:
        st.error(f"Nhập tiến độ thất bại; dữ liệu khác được giữ nguyên: {exc}")
        return

    if archive_original is not None:
        try:
            archive_original(
                "SCHEDULE_EXCEL" if kind == "excel" else "SCHEDULE_MPP",
                uploaded.name, uploaded.getvalue(),
                str(getattr(uploaded, "type", "") or ""),
            )
        except Exception as exc:
            st.warning(f"Đã nhập dữ liệu, nhưng chưa lưu được file gốc: {exc}")
    st.session_state.pop(state_key, None)
    st.session_state[accept_key] = False
    st.success(f"Đã nhập {written:,} công việc từ {kind_label}.")
    st.rerun()


def render_schedule_delete_all(
    st, db, project_id: int, *,
    can_update: bool, is_admin: bool,
) -> None:
    """Prominent one-click entry and a second-click confirmation for full wipe."""
    pid = int(project_id)
    key = f"schedule_delete_everything_open_{pid}"
    snapshot_key = f"schedule_delete_everything_count_{pid}"
    rows = db.tasks(pid, "", "Tất cả")
    count = source_counts(rows)["all"]
    st.markdown("#### 🗑 Xóa toàn bộ tiến độ")
    st.caption(f"Xóa tất cả {count:,} công việc thuộc bảng tiến độ của dự án đang chọn.")
    allowed = bool(can_update and is_admin)
    if not allowed:
        st.caption("Chỉ Admin có quyền cập nhật mới có thể xóa toàn bộ.")
    if st.button(
        "🗑 Xóa hết bảng tiến độ",
        key=f"schedule_delete_all_begin_{pid}",
        disabled=(not allowed or count == 0),
        use_container_width=True,
    ):
        st.session_state[key] = True
        st.session_state[snapshot_key] = count

    if not st.session_state.get(key):
        return

    expected = int(st.session_state.get(snapshot_key) or 0)
    st.warning(
        f"Bạn sắp xóa **{expected:,} công việc** của dự án hiện tại, gồm "
        "Excel, Microsoft Project và công việc thủ công. "
        "Thao tác không thể hoàn tác trong bảng tiến độ."
    )
    yes, no = st.columns(2)
    if yes.button(
        "✅ Xác nhận xóa tất cả",
        key=f"schedule_delete_all_confirm_{pid}",
        disabled=not allowed,
        use_container_width=True,
    ):
        try:
            deleted = delete_schedule_scope(
                db, pid, "all", expected_rows=expected, authorized=allowed,
            )
        except Exception as exc:
            st.error(f"Chưa xóa dữ liệu: {exc}")
            st.session_state[key] = False
            return
        st.session_state[key] = False
        st.session_state.pop(snapshot_key, None)
        st.success(f"Đã xóa toàn bộ {deleted:,} công việc trong bảng tiến độ.")
        st.rerun()
    if no.button(
        "Hủy", key=f"schedule_delete_all_cancel_{pid}", use_container_width=True,
    ):
        st.session_state[key] = False
        st.session_state.pop(snapshot_key, None)
        st.rerun()


__all__ = [
    "classify_schedule_file", "_read_uploaded_schedule",
    "render_schedule_import", "render_schedule_delete_all",
]
