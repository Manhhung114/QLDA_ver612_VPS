from __future__ import annotations

from typing import Any

import vo_claim_v622 as core

PATCH_VERSION = "V6.22 VO INDEPENDENT V1"


def _money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except Exception:
        return "0"


def _install_independent_mode() -> None:
    """Disable legacy synchronization from VO Excel into contract cost_variations."""
    if getattr(core, "_qlda_vo_independent_installed", False):
        return

    def _no_contract_sync(connection, order) -> None:
        return None

    core._sync_cost_variations = _no_contract_sync
    core._qlda_vo_independent_installed = True


def _detach_legacy_excel_vo_rows(db, project_id: int) -> None:
    """Remove only rows previously auto-created by the old VO Excel integration."""
    try:
        with db.connect() as connection:
            connection.execute(
                "DELETE FROM cost_variations WHERE project_id=? AND description LIKE ?",
                (int(project_id), "[VO_EXCEL]%"),
            )
    except Exception:
        # The legacy table may not exist yet on a fresh database.
        pass


def _delete_independent_vo(db, vo_id: str) -> None:
    """Delete VO storage without touching contract/main-cost records."""
    with db.connect() as connection:
        core._ensure_tables(connection)
        connection.execute(f"DELETE FROM {core.ITEMS_TABLE} WHERE vo_id=?", (str(vo_id),))
        connection.execute(f"DELETE FROM {core.WORKBOOK_TABLE} WHERE vo_id=?", (str(vo_id),))
        connection.execute(f"DELETE FROM {core.REVISIONS_TABLE} WHERE vo_id=?", (str(vo_id),))
        connection.execute(f"DELETE FROM {core.VO_TABLE} WHERE vo_id=?", (str(vo_id),))


def _summary_values(order: dict[str, Any]) -> tuple[float, float, float]:
    increase = float(order.get("increase_amount") or 0)
    decrease = float(order.get("decrease_amount") or 0)
    return increase, decrease, increase + decrease


def render_vo_ui(db, project_id: int, can_update: bool = True) -> None:
    """Render VO as an independent sheet that only tracks signed increase/decrease."""
    import streamlit as st

    _install_independent_mode()
    pid = int(project_id)
    _detach_legacy_excel_vo_rows(db, pid)

    st.markdown("#### Phát sinh tăng/giảm (VO)")
    st.caption(
        "VO là sheet độc lập, chỉ theo dõi giá trị phát sinh tăng (+) và phát sinh giảm (-). "
        "Không đối chiếu BOQ, không phê duyệt/giải ngân tại sheet VO và không tự cộng vào hợp đồng chính. "
        "Khi VO được chấp thuận, giá trị hợp đồng chính sẽ được cập nhật tại phần Hợp đồng."
    )

    uploaded = st.file_uploader(
        "Tải file VO Excel",
        type=["xlsx", "xlsm"],
        key=f"vo_independent_upload_{pid}",
        disabled=not bool(can_update),
    )

    if uploaded is not None:
        try:
            with st.spinner("Đang đọc phát sinh tăng/giảm..."):
                parsed = core.parse_vo_workbook(uploaded.getvalue(), uploaded.name)
            summary = dict(parsed.get("summary") or {})
            increase = float(summary.get("increase_amount") or 0)
            decrease = float(summary.get("decrease_amount") or 0)
            net = increase + decrease

            c1, c2, c3 = st.columns(3)
            c1.metric("Phát sinh tăng", f"{_money(increase)} đ")
            c2.metric("Phát sinh giảm", f"{_money(decrease)} đ")
            c3.metric("Chênh lệch ròng", f"{_money(net)} đ")

            meta = dict(parsed.get("metadata") or {})
            st.caption(
                f"{parsed.get('vo_code','VO')} • {uploaded.name} • "
                f"{meta.get('revision_label','R0')} • ngày {meta.get('vo_date') or '-'}"
            )
            for warning in parsed.get("warnings") or []:
                st.warning(str(warning))

            if can_update and st.button(
                "Lưu VO",
                type="primary",
                key=f"vo_independent_save_{pid}_{parsed.get('batch_id','')}",
            ):
                saved = core.save_vo(db, pid, parsed)
                _detach_legacy_excel_vo_rows(db, pid)
                st.success(
                    f"Đã lưu {saved.get('vo_code','VO')} — tăng {_money(increase)} đ, "
                    f"giảm {_money(decrease)} đ, ròng {_money(net)} đ."
                )
                st.rerun()
        except core.VOWorkbookError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Không đọc/lưu được VO: {exc}")

    orders = core.list_vos(db, pid)
    st.markdown("##### Tổng hợp phát sinh VO")
    if not orders:
        st.info("Chưa có VO nào được lưu cho dự án này.")
        return

    total_increase = sum(float(row.get("increase_amount") or 0) for row in orders)
    total_decrease = sum(float(row.get("decrease_amount") or 0) for row in orders)
    total_net = total_increase + total_decrease
    c1, c2, c3 = st.columns(3)
    c1.metric("Tổng phát sinh tăng", f"{_money(total_increase)} đ")
    c2.metric("Tổng phát sinh giảm", f"{_money(total_decrease)} đ")
    c3.metric("Tổng chênh lệch", f"{_money(total_net)} đ")

    table_rows = []
    for row in orders:
        increase, decrease, net = _summary_values(row)
        table_rows.append(
            {
                "VO": row.get("vo_code") or "",
                "Ngày": row.get("vo_date") or "",
                "Phát sinh tăng": _money(increase),
                "Phát sinh giảm": _money(decrease),
                "Chênh lệch": _money(net),
                "Revision": row.get("revision_label") or f"R{int(row.get('latest_revision') or 0)}",
                "File": row.get("filename") or "",
            }
        )
    st.dataframe(table_rows, hide_index=True, width="stretch")

    if can_update:
        st.markdown("##### Xóa VO")
        options = {str(row.get("vo_code") or row.get("vo_id")): str(row.get("vo_id") or "") for row in orders}
        selected = st.selectbox(
            "Chọn VO cần xóa",
            [""] + list(options.keys()),
            key=f"vo_independent_delete_select_{pid}",
        )
        if selected:
            confirm = st.text_input(
                f"Nhập {selected} để xác nhận xóa",
                key=f"vo_independent_delete_confirm_{pid}_{selected}",
            )
            if st.button(
                "Xóa VO",
                key=f"vo_independent_delete_button_{pid}_{selected}",
                disabled=confirm.strip() != selected,
            ):
                _delete_independent_vo(db, options[selected])
                _detach_legacy_excel_vo_rows(db, pid)
                st.success(f"Đã xóa {selected}.")
                st.rerun()


_install_independent_mode()
