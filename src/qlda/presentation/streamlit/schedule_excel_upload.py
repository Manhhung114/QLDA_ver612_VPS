from __future__ import annotations

"""Review-first Excel schedule import for the existing Schedule page.

The source workbook is read without writing to the database. The user reviews
the original calendar dates, missing dates and conflicts before an atomic
replacement of only background-Excel schedule rows.
"""

import hashlib
import tempfile
from pathlib import Path
from typing import Callable, Any

import pandas as pd

from qlda.import_engines.schedule_background import parse_schedule_excel_path
from qlda.import_engines.schedule_persistence import save_schedule_result_batched


ArchiveCallback = Callable[[str, bytes, str], Any]
_DIRECT_UPLOAD_LIMIT = 25 * 1024 * 1024


def render_schedule_excel_upload(
    st,
    db,
    project_id: int,
    *,
    status_date,
    can_update: bool,
    archive_original: ArchiveCallback | None = None,
    expanded: bool = False,
) -> None:
    """Upload, parse/preview and explicitly confirm a schedule Excel workbook."""
    pid = int(project_id)
    state_key = f"schedule_excel_review_{pid}"
    confirm_key = f"schedule_excel_confirm_{pid}"

    with st.expander("📥 Đọc file Excel tiến độ (.xlsx/.xlsm) · Kiểm tra trước khi nhập", expanded=expanded):
        st.caption(
            "Đọc file Excel mẫu hoặc tiến độ thi công nhiều dòng tiêu đề. "
            "Ngày bắt đầu, ngày kết thúc và Số ngày được lấy đúng từ Excel, "
            "không tự chuyển theo lịch Microsoft Project. "
            "Dòng chưa có ngày được giữ trống. "
            "Chỉ thay thế các công việc nhập qua luồng Excel theo lô của dự án hiện tại; "
            "công việc thủ công và MPP được giữ nguyên."
        )
        if not can_update:
            st.info("Bạn cần quyền cập nhật tiến độ để nhập file Excel.")
        uploaded = st.file_uploader(
            "Chọn file Excel tiến độ", type=["xlsx", "xlsm"],
            key=f"schedule_excel_file_{pid}", disabled=not can_update,
        )
        if uploaded is None:
            st.session_state.pop(state_key, None)
            return

        raw = uploaded.getvalue()
        if not raw or len(raw) > _DIRECT_UPLOAD_LIMIT:
            st.error("File trống hoặc lớn hơn 25 MB. Hãy dùng chức năng tải Excel lớn lên VPS ngay phía trên.")
            return
        digest = hashlib.sha256(raw).hexdigest()
        current = st.session_state.get(state_key)
        if current and current.get("sha256") != digest:
            st.session_state.pop(state_key, None)
            current = None

        if st.button(
            "🔎 Đọc và kiểm tra file Excel", key=f"schedule_excel_preview_{pid}",
            type="primary", disabled=not can_update,
        ):
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(suffix=Path(uploaded.name).suffix, delete=False) as handle:
                    handle.write(raw)
                    temporary = Path(handle.name)
                with st.spinner("Đang đối chiếu ngày, thời lượng và phân cấp công việc..."):
                    result = parse_schedule_excel_path(
                        temporary, uploaded.name, status_date=status_date,
                    )
                current = {"sha256": digest, "filename": uploaded.name, "result": result}
                st.session_state[state_key] = current
                st.session_state[confirm_key] = False
            except Exception as exc:
                st.session_state.pop(state_key, None)
                st.error(f"Không đọc được file tiến độ: {exc}")
                return
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

        if not current:
            st.info("Bấm **Đọc và kiểm tra file Excel** để xem số hạng mục và ngày trước khi ghi dữ liệu.")
            return
        result = current["result"]
        tasks = result["tasks"]
        st.success(
            f"Đã đọc sheet **{result['sheet_name']}** · tiêu đề dòng **{result['header_row']}**."
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Hạng mục đọc được", len(tasks))
        m2.metric("Có đủ hai ngày", result["dated_count"])
        m3.metric("Chưa có ngày", result["undated_count"])
        m4.metric("Dòng bỏ qua", result["skipped_rows"])

        st.dataframe(
            pd.DataFrame([{
                "Dòng Excel": task["source_row_no"],
                "WBS/STT": task["wbs"],
                "Hạng mục công việc": task["name"],
                "Bắt đầu (gốc)": task["start_date"],
                "Kết thúc (gốc)": task["end_date"],
                "Số ngày (gốc)": task["duration"],
                "Cấp": task["outline_level"],
            } for task in tasks[:200]]),
            use_container_width=True, hide_index=True,
            height=min(520, 100 + 32 * min(len(tasks), 12)),
        )
        if len(tasks) > 200:
            st.caption(f"Đang xem 200/{len(tasks)} hạng mục đầu tiên. Tất cả được nhập sau xác nhận.")
        for warning in result.get("warnings") or []:
            st.warning(warning)
        if result.get("skipped_details"):
            st.dataframe(pd.DataFrame(result["skipped_details"]), hide_index=True, use_container_width=True)
        if result.get("summary_date_conflicts"):
            with st.expander("⚠️ Các mốc nhóm khác với công việc bên trong", expanded=False):
                st.dataframe(pd.DataFrame(result["summary_date_conflicts"]), hide_index=True, use_container_width=True)
        st.checkbox(
            "Tôi đã kiểm tra các mốc ngày và đồng ý thay thế chỉ dữ liệu tiến độ đã nhập từ Excel theo lô.",
            key=confirm_key, disabled=not can_update,
        )
        if st.button(
            "✅ Xác nhận nhập tiến độ Excel", key=f"schedule_excel_commit_{pid}",
            disabled=not (can_update and st.session_state.get(confirm_key)),
        ):
            # Check that the exact file previewed is still uploaded.
            if hashlib.sha256(uploaded.getvalue()).hexdigest() != current["sha256"]:
                st.error("File đã thay đổi. Vui lòng đọc và kiểm tra lại trước khi nhập.")
                return
            try:
                with st.spinner("Đang ghi tiến độ trong một giao dịch và kiểm tra số dòng..."):
                    stats = save_schedule_result_batched(db, pid, result)
                st.success(
                    f"Đã ghi và xác minh {stats['written_rows']:,}/{stats['expected_rows']:,} hạng mục "
                    "cho dự án hiện tại. Các công việc thủ công/MPP không thay đổi."
                )
            except Exception as exc:
                st.error(f"Không thể ghi tiến độ; dữ liệu cũ được rollback: {exc}")
                return
            if archive_original is not None:
                try:
                    archive_original(
                        uploaded.name, raw,
                        str(getattr(uploaded, "type", "") or
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                    )
                except Exception as exc:
                    st.warning(f"Đã lưu công việc nhưng chưa lưu được bản Excel gốc: {exc}")
            st.session_state.pop(state_key, None)
            st.session_state[confirm_key] = False
            st.rerun()


__all__ = ["render_schedule_excel_upload"]
