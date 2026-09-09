from __future__ import annotations

from typing import Any

import vo_claim_v622 as core

PATCH_VERSION = "V6.22 VO INDEPENDENT TABS V2"


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


def _render_excel_snapshot(st, pd, book: dict[str, Any] | None) -> None:
    if not book:
        st.info("Không có snapshot workbook Excel.")
        return

    names = list(book.get("workbook_sheet_names") or [])
    snapshots = book.get("workbook") or {}
    if not names:
        st.info("Workbook không có sheet để hiển thị.")
        return

    excel_tabs = st.tabs(names)
    for name, excel_tab in zip(names, excel_tabs):
        with excel_tab:
            snap = snapshots.get(name) or {}
            rows = snap.get("rows") or []
            cols = snap.get("columns") or []
            if rows and cols:
                df = pd.DataFrame(rows, columns=cols)
                try:
                    df = df.map(core._table_value)
                except Exception:
                    pass
                st.dataframe(df, hide_index=True, width="stretch", height=520)
                if snap.get("truncated"):
                    st.caption("Preview đã giới hạn để đảm bảo hiệu năng; dữ liệu chuẩn hóa vẫn được lưu đầy đủ.")
            else:
                st.info("Sheet này không có dữ liệu hiển thị.")


def _render_vo_items(st, pd, db, vo_id: str) -> None:
    items = core.vo_items(db, vo_id)
    if not items:
        st.info("VO này chưa có dòng phát sinh chi tiết.")
        return

    df = pd.DataFrame(items).rename(
        columns={
            "sheet_name": "Sheet",
            "row_no": "Dòng",
            "description": "Hạng mục",
            "unit": "ĐVT",
            "contract_qty": "KL HĐ",
            "actual_qty": "KL thực tế",
            "variation_qty": "KL +/-",
            "unit_price_total": "Đơn giá",
            "variation_amount": "Giá trị VO",
            "variation_kind": "Loại",
            "note": "Ghi chú",
        }
    )
    keep = [
        c
        for c in (
            "Sheet",
            "Dòng",
            "Hạng mục",
            "ĐVT",
            "KL HĐ",
            "KL thực tế",
            "KL +/-",
            "Đơn giá",
            "Giá trị VO",
            "Loại",
            "Ghi chú",
        )
        if c in df.columns
    ]
    df = df[keep]
    for col in ("KL HĐ", "KL thực tế", "KL +/-", "Đơn giá", "Giá trị VO"):
        if col in df.columns:
            df[col] = df[col].map(core._table_value)
    st.dataframe(df, hide_index=True, width="stretch", height=520)


def _render_vo_history(st, pd, db, order: dict[str, Any], can_update: bool) -> None:
    vo_id = str(order.get("vo_id") or "")
    revisions = core.vo_revisions(db, vo_id)
    st.markdown("##### Lịch sử cập nhật VO")
    if revisions:
        rdf = pd.DataFrame(revisions).rename(
            columns={
                "revision_no": "Lần",
                "revision_label": "Revision",
                "filename": "File",
                "created_at": "Ngày lưu",
            }
        )
        keep = [c for c in ("Lần", "Revision", "File", "Ngày lưu") if c in rdf.columns]
        st.dataframe(rdf[keep], hide_index=True, width="stretch")
    else:
        st.info("Chưa có lịch sử revision.")

    if not can_update:
        return

    vo_code = str(order.get("vo_code") or "VO")
    st.divider()
    st.markdown("##### Xóa VO")
    st.caption("Xóa VO chỉ xóa dữ liệu của sheet VO này, không tác động đến Hợp đồng hoặc Thanh toán.")
    confirm = st.text_input(
        f"Nhập {vo_code} để xác nhận xóa",
        key=f"vo_tab_delete_confirm_{vo_id}",
    )
    if st.button(
        "Xóa VO",
        key=f"vo_tab_delete_button_{vo_id}",
        disabled=confirm.strip() != vo_code,
    ):
        _delete_independent_vo(db, vo_id)
        st.success(f"Đã xóa {vo_code}.")
        st.rerun()


def render_vo_ui(db, project_id: int, can_update: bool = True) -> None:
    """Render independent VO records using the same tab/sheet pattern as IPC payment claims."""
    import pandas as pd
    import streamlit as st

    _install_independent_mode()
    pid = int(project_id)
    _detach_legacy_excel_vo_rows(db, pid)

    st.markdown("#### Phát sinh tăng/giảm (VO)")
    st.caption(
        "VO là sheet độc lập, chỉ theo dõi phát sinh tăng (+) và phát sinh giảm (-). "
        "Mỗi VO được lưu thành một sheet riêng tương tự từng Claim trong Thanh toán & giải ngân. "
        "VO không đối chiếu BOQ, không phê duyệt/giải ngân tại đây và không tự cộng vào hợp đồng chính."
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
    labels = ["📊 Tổng hợp"] + [str(row.get("vo_code") or f"VO-{int(row.get('vo_no') or 0):02d}") for row in orders]
    tabs = st.tabs(labels)

    with tabs[0]:
        st.markdown("##### Tổng hợp phát sinh VO")
        if not orders:
            st.info("Chưa có VO nào được lưu cho dự án này.")
        else:
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

    for order, vo_tab in zip(orders, tabs[1:]):
        with vo_tab:
            vo_id = str(order.get("vo_id") or "")
            vo_code = str(order.get("vo_code") or "VO")
            increase, decrease, net = _summary_values(order)

            st.markdown(f"##### {vo_code}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Phát sinh tăng", f"{_money(increase)} đ")
            c2.metric("Phát sinh giảm", f"{_money(decrease)} đ")
            c3.metric("Chênh lệch ròng", f"{_money(net)} đ")

            sub_tabs = st.tabs(["Tổng quan", "Excel", "Chi tiết tăng/giảm", "Lịch sử"])

            with sub_tabs[0]:
                st.caption(
                    f"File: {order.get('filename','')} • Ngày VO: {order.get('vo_date') or '-'} • "
                    f"Revision: {order.get('revision_label') or 'R0'}"
                )
                info_rows = [
                    {"Thông tin": "Mã VO", "Giá trị": vo_code},
                    {"Thông tin": "Dự án", "Giá trị": order.get("project_name") or order.get("project") or ""},
                    {"Thông tin": "Gói thầu", "Giá trị": order.get("package_name") or order.get("package") or ""},
                    {"Thông tin": "Ngày VO", "Giá trị": order.get("vo_date") or ""},
                    {"Thông tin": "Revision", "Giá trị": order.get("revision_label") or "R0"},
                    {"Thông tin": "File nguồn", "Giá trị": order.get("filename") or ""},
                ]
                st.dataframe(info_rows, hide_index=True, width="stretch")
                st.info(
                    "Sheet VO chỉ ghi nhận phát sinh tăng/giảm. Giá trị này không tự cập nhật Hợp đồng "
                    "và không tạo nghiệp vụ thanh toán/giải ngân."
                )

            with sub_tabs[1]:
                _render_excel_snapshot(st, pd, core.load_vo_workbook(db, vo_id))

            with sub_tabs[2]:
                _render_vo_items(st, pd, db, vo_id)

            with sub_tabs[3]:
                _render_vo_history(st, pd, db, order, bool(can_update))


_install_independent_mode()
