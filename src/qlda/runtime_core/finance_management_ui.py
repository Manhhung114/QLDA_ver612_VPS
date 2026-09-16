from __future__ import annotations

"""Giao diện Quản lý Tài chính: Dự trù dòng tiền là một sheet riêng.

Module này không sửa dữ liệu tài chính. Nó chỉ bố trí lại màn hình hiện hữu:
- đổi tiêu đề Quản lý chi phí -> Quản lý Tài chính;
- thêm tab Dự trù dòng tiền ngang hàng BOQ / Thanh toán / VO;
- bỏ khối expander dự trù ở phía trên;
- Việt hóa các nhãn chính của Dự trù dòng tiền V3.
"""

from datetime import date, timedelta
from functools import wraps
import inspect
from typing import Any

PATCH_MARKER = "V7.6 FINANCE MANAGEMENT TABS VN V1"

_SCENARIO_VN = {
    "Base": "Cơ sở",
    "Optimistic": "Lạc quan",
    "Conservative": "Thận trọng",
}

_SEVERITY_VN = {
    "CRITICAL": "Nghiêm trọng",
    "WARNING": "Cảnh báo",
    "INFO": "Thông tin",
}

_SOURCE_VN = {
    "SCHEDULE_BOQ": "BOQ + tiến độ",
    "PAYMENT_CLAIM": "Hồ sơ thanh toán",
    "CLAIM": "Hồ sơ thanh toán",
    "PAYMENT": "Thanh toán",
    "PAYMENT_TRACKING": "Theo dõi thanh toán",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return int(default)


def _source_label(value: Any) -> str:
    raw = _text(value)
    return _SOURCE_VN.get(raw, raw.replace("_", " ") or "Khác")


def _date_text(value: Any) -> str:
    from qlda.runtime_core.cashflow_forecast_v3 import _parse_date

    d = _parse_date(value)
    return d.strftime("%d/%m/%Y") if d else ""


def _money(value: Any) -> str:
    from qlda.runtime_core.cashflow_forecast_v3 import _money

    return _money(value)


def _render_overview(st, rows: list[dict[str, Any]], start: date, end: date, scenario: str, alerts: list[dict[str, Any]]) -> None:
    import pandas as pd
    import qlda.runtime_core.cashflow_forecast_v1 as v1
    import qlda.runtime_core.cashflow_forecast_v3 as v3

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("30 ngày", _money(v1._sum_horizon(rows, 30)))
    c2.metric("60 ngày", _money(v1._sum_horizon(rows, 60)))
    c3.metric("90 ngày", _money(v1._sum_horizon(rows, 90)))
    c4.metric("6 tháng", _money(v1._sum_horizon(rows, 180)))
    c5.metric("12 tháng", _money(v1._sum_horizon(rows, 365)))

    summary = v3.summarize_rows(rows, start, end)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Dự báo • {_SCENARIO_VN.get(scenario, scenario)}", _money(summary["forecast"]))
    c2.metric("Dòng tiền kỳ vọng", _money(summary["expected"]))
    c3.metric("Đã duyệt chờ thanh toán", _money(summary["approved_waiting"]))
    c4.metric("Số cảnh báo", len(alerts))

    monthly = v1.aggregate_monthly(rows, start, end)
    if monthly:
        frame = pd.DataFrame([
            {
                "Tháng": v1._month_label(r["month"]),
                "Kế hoạch": r["plan"],
                "Dự báo": r["forecast"],
                "Kỳ vọng": r["expected"],
                "Thực tế": r["actual"],
            }
            for r in monthly
        ])
        st.markdown("#### Kế hoạch / Dự báo / Kỳ vọng / Thực tế")
        st.bar_chart(frame.set_index("Tháng"), use_container_width=True)
        view = frame.copy()
        for col in ("Kế hoạch", "Dự báo", "Kỳ vọng", "Thực tế"):
            view[col] = view[col].map(lambda x: f"{float(x):,.0f}")
        st.dataframe(view, hide_index=True, use_container_width=True)


def _render_scenarios_vn(st, db, pid: int, start: date, end: date) -> None:
    import pandas as pd
    import qlda.runtime_core.cashflow_forecast_v3 as v3

    data = v3.scenario_summary(db, pid, start, end)
    cols = st.columns(3)
    for col, item in zip(cols, data):
        label = _SCENARIO_VN.get(_text(item.get("scenario")), _text(item.get("scenario")))
        col.metric(label, _money(item.get("expected")), delta=f"Dự báo {_money(item.get('forecast'))}")
    frame = pd.DataFrame([
        {
            "Kịch bản": _SCENARIO_VN.get(_text(x.get("scenario")), _text(x.get("scenario"))),
            "Kế hoạch": x.get("plan", 0),
            "Dự báo": x.get("forecast", 0),
            "Kỳ vọng": x.get("expected", 0),
        }
        for x in data
    ])
    st.dataframe(frame, hide_index=True, use_container_width=True)
    if not frame.empty:
        st.bar_chart(frame.set_index("Kịch bản")[["Kỳ vọng"]], use_container_width=True)


def _render_alerts_vn(st, alerts: list[dict[str, Any]]) -> None:
    import pandas as pd

    if not alerts:
        st.success("Không phát hiện cảnh báo dòng tiền theo các ngưỡng hiện tại.")
        return
    critical = sum(1 for a in alerts if _text(a.get("severity")) == "CRITICAL")
    warning = sum(1 for a in alerts if _text(a.get("severity")) == "WARNING")
    info = sum(1 for a in alerts if _text(a.get("severity")) == "INFO")
    c1, c2, c3 = st.columns(3)
    c1.metric("Nghiêm trọng", critical)
    c2.metric("Cảnh báo", warning)
    c3.metric("Thông tin", info)
    frame = pd.DataFrame([
        {
            "Mức": _SEVERITY_VN.get(_text(a.get("severity")), _text(a.get("severity"))),
            "Mã": a.get("code", ""),
            "Nội dung": a.get("title", ""),
            "Giá trị": a.get("amount", 0),
            "Chi tiết": a.get("detail", ""),
        }
        for a in alerts
    ])
    st.dataframe(frame, hide_index=True, use_container_width=True)


def _render_delay_vn(st, rows: list[dict[str, Any]], start: date, end: date, pid: int) -> None:
    import pandas as pd
    import qlda.runtime_core.cashflow_forecast_v3 as v3

    st.caption("Mô phỏng chỉ dịch ngày dự báo của phần BOQ + tiến độ; không sửa dữ liệu gốc và không thay đổi giá trị tiền.")
    schedule_rows = [r for r in rows if _text(r.get("source_type")) == "SCHEDULE_BOQ"]
    contractors = sorted({_text(r.get("contractor")) for r in schedule_rows if _text(r.get("contractor"))})
    c1, c2 = st.columns(2)
    delay = c1.selectbox(
        "Giả định chậm tiến độ",
        [0, 15, 30, 45, 60, 90],
        index=2,
        format_func=lambda x: f"{x} ngày",
        key=f"cashflow_finance_delay_{pid}",
    )
    selected = c2.selectbox(
        "Phạm vi mô phỏng",
        ["Tất cả nhà thầu"] + contractors,
        key=f"cashflow_finance_delay_contractor_{pid}",
    )
    contractor = "" if selected == "Tất cả nhà thầu" else selected
    base = {r["month"]: r["expected"] for r in v3.monthly_expected(rows, start, end)}
    shifted = {
        r["month"]: r["expected"]
        for r in v3.monthly_expected(rows, start, end, shift_days=int(delay), contractor=contractor)
    }
    months = sorted(set(base) | set(shifted))
    frame = pd.DataFrame([
        {
            "Tháng": m,
            "Hiện tại": base.get(m, 0.0),
            f"Sau khi trễ {delay} ngày": shifted.get(m, 0.0),
            "Chênh lệch": shifted.get(m, 0.0) - base.get(m, 0.0),
        }
        for m in months
    ])
    if frame.empty:
        st.info("Không có dòng BOQ + tiến độ trong kỳ để mô phỏng.")
        return
    st.bar_chart(frame.set_index("Tháng")[["Hiện tại", f"Sau khi trễ {delay} ngày"]], use_container_width=True)
    st.dataframe(frame, hide_index=True, use_container_width=True)


def _render_detail_vn(st, db, pid: int, rows: list[dict[str, Any]], *, identity: Any, can_update: bool) -> None:
    import pandas as pd
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    if not rows:
        st.info("Chưa có dữ liệu dự báo.")
        return
    table = pd.DataFrame([
        {
            "Nguồn": _source_label(r.get("source_type")),
            "IPC/BOQ": r.get("claim_code", ""),
            "Nhà thầu": r.get("contractor", ""),
            "Trạng thái": r.get("status", ""),
            "Công việc": r.get("task_name", ""),
            "Hạng mục BOQ": r.get("boq_item", ""),
            "Ngày đến hạn": _date_text(r.get("due_date")),
            "Ngày dự báo": _date_text(r.get("forecast_date")),
            "Giá trị dự báo": _float(r.get("outstanding")),
            "Xác suất": f"{_float(r.get('probability')) * 100:.0f}%",
            "Giá trị kỳ vọng": _float(r.get("expected_amount")),
            "Đã giải ngân": _float(r.get("disbursed_amount")),
            "Căn cứ ngày": r.get("due_source", ""),
            "Ghi chú": r.get("note", ""),
        }
        for r in rows
    ])
    st.dataframe(table, hide_index=True, use_container_width=True)
    st.download_button(
        "⬇️ Xuất bảng dự báo CSV",
        data=table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"du_tru_dong_tien_{pid}_{date.today():%Y%m%d}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.markdown("#### Điều chỉnh một khoản dự báo")
    labels = {
        f"{r['source_type']}|{r['source_key']}": (
            f"{r.get('claim_code') or r.get('source_key')} • {r.get('contractor','')} • {_money(r.get('outstanding'))}"
        )
        for r in rows if _text(r.get("source_key"))
    }
    if not labels:
        return
    selected_key = st.selectbox(
        "Khoản cần điều chỉnh",
        list(labels),
        format_func=lambda key: labels.get(key, key),
        key=f"cashflow_finance_edit_select_{pid}",
    )
    current = next(r for r in rows if f"{r['source_type']}|{r['source_key']}" == selected_key)
    auto_date = not bool(current.get("has_override")) or _text(current.get("due_source")) != "Điều chỉnh thủ công"
    with st.form(f"cashflow_finance_override_{pid}_{current['source_type']}_{current['source_key']}"):
        use_auto_date = st.checkbox("Dùng ngày dự báo tự động", value=auto_date)
        forecast_date_value = st.date_input(
            "Ngày dự kiến thanh toán",
            value=current.get("forecast_date") or date.today(),
            disabled=use_auto_date,
        )
        use_source_amount = st.checkbox(
            "Dùng giá trị dự báo tự động",
            value=abs(_float(current.get("planned_amount")) - _float(current.get("outstanding"))) < 1e-6,
        )
        planned_amount = st.number_input(
            "Giá trị kế hoạch (VND)",
            min_value=0.0,
            value=float(current.get("planned_amount") or 0),
            step=1_000_000.0,
            disabled=use_source_amount,
        )
        auto_prob = st.checkbox(
            "Dùng xác suất tự động theo nguồn/trạng thái",
            value=_text(current.get("probability_reason")) != "Điều chỉnh thủ công",
        )
        prob_pct = st.number_input(
            "Xác suất kịch bản cơ sở (%)",
            min_value=0.0,
            max_value=100.0,
            value=float(current.get("base_probability") or 0) * 100.0,
            step=5.0,
            disabled=auto_prob,
        )
        note = st.text_area("Ghi chú dự báo", value=_text(current.get("note")))
        save = st.form_submit_button("💾 Lưu điều chỉnh", disabled=not can_update, use_container_width=True)
        if save:
            v1.save_override(
                db,
                pid,
                current["source_type"],
                current["source_key"],
                forecast_date=None if use_auto_date else forecast_date_value,
                planned_amount=0.0 if use_source_amount else planned_amount,
                probability=-1.0 if auto_prob else prob_pct / 100.0,
                note=note,
                actor=identity,
            )
            st.success("Đã lưu điều chỉnh dự báo.")
            st.rerun()
    if st.button(
        "↩️ Xóa điều chỉnh, dùng lại dữ liệu tự động",
        key=f"cashflow_finance_clear_{pid}_{current['source_type']}_{current['source_key']}",
        disabled=(not can_update or not current.get("has_override")),
        use_container_width=True,
    ):
        v1.clear_override(db, pid, current["source_type"], current["source_key"])
        st.success("Đã trả khoản dự báo về chế độ tự động.")
        st.rerun()


def _render_schedule_vn(st, diagnostics: dict[str, Any]) -> None:
    import pandas as pd

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BOQ có liên kết tiến độ", f"{_int(diagnostics.get('linked_rows')):,} dòng")
    c2.metric("BOQ chưa liên kết", f"{_int(diagnostics.get('unlinked_rows')):,} dòng")
    c3.metric("Giá trị chưa liên kết", _money(diagnostics.get("unlinked_value")))
    c4.metric("Dự báo từ BOQ", _money(diagnostics.get("forecast_value")))
    latest = _text(diagnostics.get("latest_claim_code")) or "Chưa có IPC"
    st.caption(
        f"Mốc chống cộng trùng: {latest}; giá trị chứng nhận lũy kế {_money(diagnostics.get('latest_certified_total'))}. "
        f"Độ trễ từ mốc khối lượng đến thanh toán: {_int(diagnostics.get('payment_lag_days'))} ngày."
    )
    data = diagnostics.get("task_rows") or []
    if data:
        view = pd.DataFrame([
            {
                "BOQ ID": r.get("boq_id"),
                "Công việc": r.get("task_name"),
                "Hạng mục BOQ": r.get("boq_item"),
                "Giá trị BOQ": r.get("budget_total"),
                "Đã chứng nhận": r.get("certified"),
                "Cách đối chiếu IPC": r.get("certified_method"),
                "Thực tế (%)": r.get("actual_progress"),
                "Kế hoạch hôm nay (%)": r.get("planned_progress"),
                "Kết thúc kế hoạch": r.get("planned_finish"),
                "Kết thúc dự báo": r.get("projected_finish"),
                "Giá trị dự báo còn lại": r.get("forecast_remaining"),
            }
            for r in data
        ])
        st.dataframe(view, hide_index=True, use_container_width=True)
    else:
        st.info("Chưa có BOQ liên kết công việc đủ dữ liệu để sinh dự báo.")
    if _int(diagnostics.get("unlinked_rows")) > 0:
        st.warning("Các dòng BOQ chưa gắn công việc được loại khỏi dự báo để tránh tự gán ngày thanh toán sai.")


def _render_settings_vn(st, db, pid: int, *, identity: Any, can_update: bool) -> None:
    import qlda.runtime_core.cashflow_forecast_v1 as v1
    import qlda.runtime_core.cashflow_forecast_v2 as v2
    import qlda.runtime_core.cashflow_forecast_v3 as v3

    s1 = v1.get_settings(db, pid)
    s2 = v2.get_settings(db, pid)
    s3 = v3.get_settings(db, pid)

    st.markdown("#### Giả định dự báo và thanh toán")
    with st.form(f"cashflow_finance_settings_{pid}"):
        c1, c2, c3 = st.columns(3)
        terms = c1.number_input("Điều khoản thanh toán sau khi duyệt (ngày)", 0, 365, _int(s1.get("payment_terms_days"), 30), 1)
        prep = c2.number_input("Chuẩn bị/trình IPC (ngày)", 0, 90, _int(s2.get("claim_preparation_days"), 5), 1)
        approval = c3.number_input("Kiểm tra/phê duyệt IPC (ngày)", 0, 180, _int(s2.get("approval_days"), 7), 1)
        cycle = st.number_input("Chu kỳ dự kiến lập IPC (ngày)", 7, 90, _int(s2.get("forecast_cycle_days"), 30), 1)

        c1, c2, c3, c4, c5 = st.columns(5)
        draft = c1.number_input("IPC nháp (%)", 0.0, 100.0, _float(s1.get("prob_draft"), 0.60) * 100.0, 5.0)
        submitted = c2.number_input("IPC đã trình (%)", 0.0, 100.0, _float(s1.get("prob_submitted"), 0.85) * 100.0, 5.0)
        approved = c3.number_input("IPC đã duyệt (%)", 0.0, 100.0, _float(s1.get("prob_approved"), 1.0) * 100.0, 5.0)
        earned = c4.number_input("KL đã làm chưa IPC (%)", 0.0, 100.0, _float(s2.get("prob_earned_unclaimed"), 0.70) * 100.0, 5.0)
        future = c5.number_input("BOQ + tiến độ tương lai (%)", 0.0, 100.0, _float(s2.get("prob_schedule_boq"), 0.50) * 100.0, 5.0)

        c1, c2 = st.columns(2)
        opt = c1.number_input("Kịch bản lạc quan: điều chỉnh xác suất (%)", -100.0, 100.0, _float(s1.get("optimistic_delta"), 0.15) * 100.0, 5.0)
        con = c2.number_input("Kịch bản thận trọng: điều chỉnh xác suất (%)", -100.0, 100.0, _float(s1.get("conservative_delta"), -0.20) * 100.0, 5.0)

        st.markdown("#### Ngưỡng cảnh báo")
        c1, c2, c3, c4 = st.columns(4)
        jump = c1.number_input("Tháng tăng cao hơn bình quân (%)", 0.0, 500.0, _float(s3.get("alert_month_jump_pct"), 35.0), 5.0)
        conc = c2.number_input("Tập trung theo nhà thầu (%)", 1.0, 100.0, _float(s3.get("alert_contractor_concentration_pct"), 50.0), 5.0)
        unlinked = c3.number_input("BOQ chưa liên kết tiến độ (%)", 0.0, 100.0, _float(s3.get("alert_unlinked_boq_pct"), 10.0), 5.0)
        due = c4.number_input("IPC đã duyệt sắp đến hạn (ngày)", 1, 365, _int(s3.get("approved_due_days"), 30), 1)

        save = st.form_submit_button("💾 Lưu giả định", disabled=not can_update, use_container_width=True)
        if save:
            v1.save_settings(
                db,
                pid,
                {
                    "payment_terms_days": terms,
                    "prob_draft": draft / 100.0,
                    "prob_submitted": submitted / 100.0,
                    "prob_approved": approved / 100.0,
                    "optimistic_delta": opt / 100.0,
                    "conservative_delta": con / 100.0,
                },
                actor=identity,
            )
            v2.save_settings(
                db,
                pid,
                {
                    "claim_preparation_days": prep,
                    "approval_days": approval,
                    "forecast_cycle_days": cycle,
                    "prob_earned_unclaimed": earned / 100.0,
                    "prob_schedule_boq": future / 100.0,
                },
                actor=identity,
            )
            v3.save_settings(
                db,
                pid,
                {
                    "alert_month_jump_pct": jump,
                    "alert_contractor_concentration_pct": conc,
                    "alert_unlinked_boq_pct": unlinked,
                    "approved_due_days": due,
                },
                actor=identity,
            )
            st.success("Đã lưu giả định dự trù dòng tiền.")
            st.rerun()


def render_cashflow_finance_sheet(st, db, pid: int, *, identity: Any = None, can_update: bool = False, is_admin: bool = False) -> None:
    del is_admin
    import qlda.runtime_core.cashflow_forecast_v1 as v1
    import qlda.runtime_core.cashflow_forecast_v2 as v2
    import qlda.runtime_core.cashflow_forecast_v3 as v3

    pid = int(pid)
    v1.ensure_schema(db)
    v2.ensure_schema(db)
    v3.ensure_schema(db)

    st.markdown("### 💸 Dự trù dòng tiền")
    st.caption(
        f"Phạm vi: {v1._scope_label(db, pid)}. Dữ liệu tổng hợp từ IPC/thanh toán, BOQ và tiến độ; "
        "kịch bản và mô phỏng chỉ phục vụ phân tích, không ghi đè dữ liệu gốc."
    )

    c1, c2, c3 = st.columns([1.2, 1, 1.2])
    horizon_label = c1.selectbox(
        "Kỳ dự báo",
        list(v1.HORIZONS.keys()),
        index=2,
        key=f"cashflow_finance_horizon_{pid}",
    )
    scenario = c2.selectbox(
        "Kịch bản",
        list(v1.SCENARIOS),
        index=0,
        format_func=lambda value: _SCENARIO_VN.get(value, value),
        key=f"cashflow_finance_scenario_{pid}",
    )
    start = date.today()
    days = v1.HORIZONS[horizon_label]
    if days > 0:
        end = start + timedelta(days=days)
        c3.date_input("Đến ngày", value=end, disabled=True, key=f"cashflow_finance_end_{pid}")
    else:
        end = c3.date_input(
            "Đến ngày tùy chọn",
            value=start + timedelta(days=365),
            min_value=start,
            max_value=start + timedelta(days=365),
            key=f"cashflow_finance_custom_end_{pid}",
        )
        if end < start:
            end = start

    rows = v1.build_forecast_rows(db, pid, scenario=scenario)
    _, diagnostics = v2.build_schedule_boq_rows(db, pid, scenario=scenario)
    alerts = v3.build_alerts(db, pid, rows, diagnostics, start, end)

    tabs = st.tabs([
        "Tổng quan",
        "Kịch bản",
        "Cảnh báo",
        "Mô phỏng trễ",
        "Chi tiết dự báo",
        "BOQ & tiến độ",
        "Giả định",
    ])
    with tabs[0]:
        _render_overview(st, rows, start, end, scenario, alerts)
    with tabs[1]:
        _render_scenarios_vn(st, db, pid, start, end)
    with tabs[2]:
        _render_alerts_vn(st, alerts)
    with tabs[3]:
        _render_delay_vn(st, rows, start, end, pid)
    with tabs[4]:
        _render_detail_vn(st, db, pid, rows, identity=identity, can_update=can_update)
    with tabs[5]:
        _render_schedule_vn(st, diagnostics)
    with tabs[6]:
        _render_settings_vn(st, db, pid, identity=identity, can_update=can_update)


def install_finance_management_ui() -> None:
    """Đổi tiêu đề và thêm sheet Dự trù dòng tiền vào render_cost_management."""
    import streamlit as st

    if getattr(st, "_qlda_finance_management_ui_installed", False):
        return

    previous_subheader = st.subheader
    previous_tabs = st.tabs

    @wraps(previous_subheader)
    def finance_subheader(body, *args, **kwargs):
        # Việc truyền tiêu đề mới vào wrapper cũ cũng vô hiệu hóa panel dự trù
        # được V1 chèn phía trên bằng điều kiện khớp tiêu đề "Quản lý chi phí".
        if _text(body) == "💰 Quản lý chi phí":
            body = "💰 Quản lý Tài chính"
        return previous_subheader(body, *args, **kwargs)

    @wraps(previous_tabs)
    def finance_tabs(labels, *args, **kwargs):
        label_list = list(labels) if isinstance(labels, (list, tuple)) else labels
        frame = inspect.currentframe()
        caller = frame.f_back if frame else None
        try:
            is_cost_screen = (
                caller is not None
                and caller.f_code.co_name == "render_cost_management"
                and isinstance(label_list, list)
                and label_list == ["Chi phí dự toán (BOQ)", "Thanh toán & giải ngân", "Chi phí phát sinh (VO)"]
            )
            if not is_cost_screen:
                return previous_tabs(labels, *args, **kwargs)

            all_tabs = previous_tabs(
                ["Chi phí dự toán (BOQ)", "Thanh toán & giải ngân", "Chi phí phát sinh (VO)", "Dự trù dòng tiền"],
                *args,
                **kwargs,
            )
            glb, loc = caller.f_globals, caller.f_locals
            db = glb.get("db")
            pid = _int(loc.get("pid"))
            if db is not None and pid > 0:
                can_update_fn = glb.get("_can_update")
                is_admin_fn = glb.get("_is_admin")
                identity_fn = glb.get("_cloud_identity")
                with all_tabs[3]:
                    render_cashflow_finance_sheet(
                        st,
                        db,
                        pid,
                        identity=identity_fn() if callable(identity_fn) else None,
                        can_update=bool(can_update_fn()) if callable(can_update_fn) else False,
                        is_admin=bool(is_admin_fn()) if callable(is_admin_fn) else False,
                    )
            return all_tabs[:3]
        finally:
            del caller
            del frame

    st.subheader = finance_subheader
    st.tabs = finance_tabs
    st._qlda_finance_management_ui_installed = True
    st._qlda_finance_management_ui_marker = PATCH_MARKER


__all__ = ["render_cashflow_finance_sheet", "install_finance_management_ui"]
