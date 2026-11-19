from __future__ import annotations

"""Shared Streamlit presentation for Excel/MPP schedule rows and safe deletion."""

from typing import Any

from qlda.infrastructure.schedule_workspace import (
    delete_schedule_scope, filtered_schedule_rows, source_counts,
)


_FILTER_LABELS = {
    "all": "Tất cả nguồn",
    "excel": "Excel",
    "mpp": "Microsoft Project (.mpp)",
    "manual": "Thủ công / Excel nhập kiểu cũ",
    "other": "Nguồn khác",
}


def render_schedule_source_filter(st, *, project_id: int, rows: list[Any], all_rows: list[Any]) -> list[Any]:
    """Apply only a display filter. Never modify the database or schedule dates."""
    counts = source_counts(all_rows)
    options = ["all"] + [
        kind for kind in ("excel", "mpp", "manual", "other") if counts[kind] > 0
    ]
    selected = st.selectbox(
        "Nguồn tiến độ hiển thị",
        options,
        format_func=lambda kind: f"{_FILTER_LABELS[kind]} ({counts[kind]:,})",
        key=f"schedule_source_filter_{int(project_id)}",
    )
    st.caption(
        "Excel và Microsoft Project dùng chung một bảng công việc, Gantt và theo dõi KH%/TT%. "
        "Bộ lọc chỉ thay đổi dữ liệu hiển thị, không đổi lịch hoặc dữ liệu gốc."
    )
    return filtered_schedule_rows(rows, selected)


def render_schedule_delete_panel(
    st, db, project_id: int, *, project: Any,
    rows: list[Any], can_update: bool, is_admin: bool,
) -> None:
    """Offer separate Excel/MPP/manual/all deletion with typed confirmation."""
    pid = int(project_id)
    counts = source_counts(rows)
    with st.expander("🗑 Xóa tiến độ khi cần (chọn nguồn cần xóa)", expanded=False):
        st.caption(
            "Chỉ xóa các công việc trong dự án/nhà thầu đang chọn. "
            "Có thể xóa riêng Excel hoặc Microsoft Project mà không ảnh hưởng nguồn còn lại. "
            "File gốc đã lưu, BOQ, IPC/Claim, VO, hồ sơ và bản vẽ không bị xóa."
        )
        summary = " · ".join([
            f"Excel: {counts['excel']:,}",
            f"Project: {counts['mpp']:,}",
            f"Thủ công/Excel cũ: {counts['manual']:,}",
            f"Khác: {counts['other']:,}",
        ])
        st.write(f"**Dữ liệu hiện có:** {summary}")
        if not can_update:
            st.info("Tài khoản cần quyền cập nhật tiến độ để sử dụng chức năng xóa.")
            return
        if counts["all"] == 0:
            st.info("Bảng tiến độ hiện chưa có công việc để xóa.")
            return

        scopes = [kind for kind in ("excel", "mpp") if counts[kind]]
        if is_admin:
            if counts["manual"]:
                scopes.append("manual")
            scopes.append("all")

        with st.form(f"delete_schedule_source_form_{pid}", clear_on_submit=False):
            scope = st.selectbox(
                "Chọn dữ liệu tiến độ cần xóa",
                scopes,
                format_func=lambda value: f"{_FILTER_LABELS[value]} · {counts[value]:,} hạng mục",
                key=f"schedule_delete_scope_{pid}",
            )
            project_name = str(project["name"] or "") if project is not None else ""
            st.warning(
                f"Bạn sắp xóa **{counts[scope]:,} hạng mục** thuộc "
                f"**{_FILTER_LABELS[scope]}** của dự án **{project_name or pid}**. "
                "Thao tác xóa không thể hoàn tác trong bảng tiến độ."
            )
            st.caption(
                "Lưu ý: Excel được nhập bằng chức năng cũ có thể mang nhãn 'Thủ công'. "
                "Vì vậy nút xóa riêng Excel không bao gồm các dòng đó, để tránh xóa nhầm."
            )
            acknowledge = st.checkbox("Tôi hiểu phạm vi xóa và đã kiểm tra số hạng mục.")
            confirmation = st.text_input(
                'Nhập chính xác "XOA TIEN DO" để xác nhận',
                value="", key=f"schedule_delete_confirmation_{pid}",
            )
            submitted = st.form_submit_button(
                "🗑 Xóa tiến độ đã chọn", disabled=not can_update,
            )

        if not submitted:
            return
        if not acknowledge or confirmation.strip().upper() != "XOA TIEN DO":
            st.error("Chưa xác nhận đủ. Dữ liệu chưa bị xóa.")
            return
        if scope in {"manual", "all"} and not is_admin:
            st.error("Chỉ Admin mới được xóa công việc thủ công hoặc toàn bộ tiến độ.")
            return
        try:
            affected = delete_schedule_scope(
                db, pid, scope, expected_rows=counts[scope],
                authorized=can_update and (scope in {"excel", "mpp"} or is_admin),
            )
        except Exception as exc:
            st.error(f"Không thể xóa tiến độ: {exc}")
            return
        st.success(f"Đã xóa đúng {affected:,} hạng mục {_FILTER_LABELS[scope]}.")
        st.rerun()


__all__ = ["render_schedule_delete_panel", "render_schedule_source_filter"]
