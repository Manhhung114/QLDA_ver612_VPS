from __future__ import annotations

from typing import Any

import qlda.runtime_core.vo_claim as core

PATCH_VERSION = "V6.22 VO INDEPENDENT TABS V3 APPROVAL"
VO_STATUSES = [
    "Dự thảo",
    "Đã trình",
    "Đang duyệt",
    "Yêu cầu chỉnh sửa",
    "Đã duyệt",
    "Từ chối",
    "Đóng",
]


def _money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except Exception:
        return "0"


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


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
    increase = _number(order.get("increase_amount"))
    decrease = _number(order.get("decrease_amount"))
    return increase, decrease, increase + decrease


def _proposed_value(order: dict[str, Any]) -> float:
    stored = order.get("proposed_amount")
    if stored not in (None, ""):
        return _number(stored)
    return _summary_values(order)[2]


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


def _render_vo_approval(st, db, order: dict[str, Any], can_update: bool) -> None:
    vo_id = str(order.get("vo_id") or "")
    proposed = _proposed_value(order)
    current_approved = _number(order.get("approved_amount"))
    current_status = str(order.get("status") or "Dự thảo").strip() or "Dự thảo"
    if current_status not in VO_STATUSES:
        VO_STATUSES.append(current_status)

    st.markdown("##### Cập nhật phê duyệt VO")
    st.caption(
        "Giá trị đề xuất lấy từ VO Excel. Giá trị được duyệt được cập nhật độc lập và có thể khác giá trị đề xuất. "
        "VO giảm/điều chỉnh giảm phải giữ dấu âm khi phê duyệt."
    )

    k1, k2, k3 = st.columns(3)
    k1.metric("Giá trị đề xuất", f"{_money(proposed)} đ")
    k2.metric("Giá trị được duyệt", f"{_money(current_approved)} đ")
    k3.metric("Trạng thái hiện tại", current_status)

    with st.form(f"vo_approval_form_{vo_id}"):
        c1, c2 = st.columns(2)
        status = c1.selectbox(
            "Trạng thái phê duyệt",
            VO_STATUSES,
            index=VO_STATUSES.index(current_status),
            disabled=not bool(can_update),
        )
        use_proposed = c2.checkbox(
            "Duyệt theo đúng giá trị đề xuất",
            value=False,
            disabled=not bool(can_update),
            help="Khi chọn, hệ thống dùng nguyên giá trị đề xuất của VO, bao gồm cả dấu âm nếu là VO giảm.",
        )

        approved_input = st.number_input(
            "Giá trị được duyệt (VND)",
            value=float(current_approved),
            step=1_000_000.0,
            disabled=not bool(can_update) or bool(use_proposed),
        )
        effective_approved = float(proposed if use_proposed else approved_input)
        if use_proposed:
            st.info(f"Giá trị sẽ lưu: {_money(effective_approved)} VND")

        c1, c2 = st.columns(2)
        funding = c1.text_input(
            "Nguồn vốn / nguồn ngân sách",
            value=str(order.get("funding_source") or ""),
            disabled=not bool(can_update),
        )
        vo_date = c2.date_input(
            "Ngày VO / ngày phê duyệt",
            value=core._parse_ui_date(order.get("vo_date")),
            disabled=not bool(can_update),
        )
        note = st.text_area(
            "Ghi chú / căn cứ phê duyệt",
            value=str(order.get("note") or ""),
            disabled=not bool(can_update),
        )

        submit = st.form_submit_button(
            "💾 Cập nhật duyệt VO",
            type="primary",
            disabled=not bool(can_update),
            width="stretch",
        )

    if submit:
        if status == "Đã duyệt" and abs(effective_approved) < 1e-9 and abs(proposed) > 1e-9:
            st.warning("VO đang chuyển sang Đã duyệt nhưng giá trị được duyệt bằng 0. Hãy kiểm tra lại trước khi lưu.")
            return
        core.update_vo_finance(
            db,
            vo_id,
            approved_amount=effective_approved,
            status=status,
            funding_source=funding,
            vo_date=vo_date.strftime("%Y-%m-%d"),
            note=note,
        )
        st.success("Đã cập nhật trạng thái và giá trị phê duyệt VO.")
        st.rerun()


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

    st.markdown("##### Trạng thái phê duyệt hiện tại")
    st.dataframe(
        [{
            "Trạng thái": order.get("status") or "Dự thảo",
            "Giá trị đề xuất": _money(_proposed_value(order)),
            "Giá trị được duyệt": _money(order.get("approved_amount")),
            "Nguồn vốn": order.get("funding_source") or "",
            "Ghi chú": order.get("note") or "",
            "Cập nhật": order.get("updated_at") or "",
        }],
        hide_index=True,
        width="stretch",
    )

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
    """Render independent VO records and maintain their approval metadata."""
    import pandas as pd
    import streamlit as st

    _install_independent_mode()
    pid = int(project_id)
    _detach_legacy_excel_vo_rows(db, pid)

    st.markdown("#### Phát sinh tăng/giảm (VO)")
    st.caption(
        "VO được lưu độc lập theo từng sheet. Giá trị tăng/giảm lấy từ Excel; trạng thái và giá trị phê duyệt được cập nhật tại tab Phê duyệt. "
        "VO được duyệt không tự cộng vào Hợp đồng chính để tránh cộng trùng khi đã có Phụ lục hợp đồng."
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
            increase = _number(summary.get("increase_amount"))
            decrease = _number(summary.get("decrease_amount"))
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
            total_increase = sum(_number(row.get("increase_amount")) for row in orders)
            total_decrease = sum(_number(row.get("decrease_amount")) for row in orders)
            total_net = total_increase + total_decrease
            total_approved = sum(_number(row.get("approved_amount")) for row in orders if str(row.get("status") or "") == "Đã duyệt")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Tổng phát sinh tăng", f"{_money(total_increase)} đ")
            c2.metric("Tổng phát sinh giảm", f"{_money(total_decrease)} đ")
            c3.metric("Tổng chênh lệch", f"{_money(total_net)} đ")
            c4.metric("VO đã duyệt", f"{_money(total_approved)} đ")

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
                        "Được duyệt": _money(row.get("approved_amount")),
                        "Trạng thái": row.get("status") or "Dự thảo",
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
            approved = _number(order.get("approved_amount"))

            st.markdown(f"##### {vo_code}")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Phát sinh tăng", f"{_money(increase)} đ")
            c2.metric("Phát sinh giảm", f"{_money(decrease)} đ")
            c3.metric("Chênh lệch ròng", f"{_money(net)} đ")
            c4.metric("Được duyệt", f"{_money(approved)} đ", str(order.get("status") or "Dự thảo"))

            sub_tabs = st.tabs(["Tổng quan", "Excel", "Chi tiết tăng/giảm", "Phê duyệt", "Lịch sử"])

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
                    {"Thông tin": "Trạng thái", "Giá trị": order.get("status") or "Dự thảo"},
                    {"Thông tin": "Giá trị đề xuất", "Giá trị": f"{_money(_proposed_value(order))} VND"},
                    {"Thông tin": "Giá trị được duyệt", "Giá trị": f"{_money(approved)} VND"},
                    {"Thông tin": "Revision", "Giá trị": order.get("revision_label") or "R0"},
                    {"Thông tin": "File nguồn", "Giá trị": order.get("filename") or ""},
                ]
                st.dataframe(info_rows, hide_index=True, width="stretch")
                st.info(
                    "VO được duyệt vẫn được theo dõi độc lập. Hệ thống không tự cộng VO vào Hợp đồng chính; "
                    "khi VO được hợp thức hóa bằng Phụ lục, Chi phí đã cam kết sẽ lấy từ Hợp đồng/Phụ lục để tránh cộng trùng."
                )

            with sub_tabs[1]:
                _render_excel_snapshot(st, pd, core.load_vo_workbook(db, vo_id))

            with sub_tabs[2]:
                _render_vo_items(st, pd, db, vo_id)

            with sub_tabs[3]:
                _render_vo_approval(st, db, order, bool(can_update))

            with sub_tabs[4]:
                _render_vo_history(st, pd, db, order, bool(can_update))


_install_independent_mode()
