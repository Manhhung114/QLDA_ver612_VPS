from __future__ import annotations

"""V3 dự trù dòng tiền: scenario + cảnh báo + mô phỏng chậm tiến độ.

V3 không thay đổi dữ liệu nguồn. V1 vẫn giữ Claim/Payment, V2 sinh phần BOQ +
tiến độ tương lai; V3 chỉ phân tích các dòng forecast đã hợp nhất để phục vụ điều
hành và làm nguồn cho Trợ lý AI chung.
"""

from datetime import date, datetime, timedelta
from typing import Any

PATCH_MARKER = "V7.6 CASHFLOW FORECAST V3 AI SCENARIO ALERTS"
SETTINGS_TABLE = "cashflow_forecast_v3_settings"

DEFAULTS = {
    "alert_month_jump_pct": 35.0,
    "alert_contractor_concentration_pct": 50.0,
    "alert_unlinked_boq_pct": 10.0,
    "approved_due_days": 30,
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


def _rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:19] if "%H" in fmt else text[:10], fmt).date()
        except Exception:
            pass
    return None


def _money(value: Any) -> str:
    n = _float(value)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000_000:
        return f"{sign}{n / 1_000_000_000:,.2f} tỷ"
    if n >= 1_000_000:
        return f"{sign}{n / 1_000_000:,.1f} triệu"
    return f"{sign}{n:,.0f} đ"


def _actor_name(identity: Any) -> str:
    row = _rowdict(identity)
    return _text(row.get("name") or row.get("email") or "Người dùng")


def ensure_schema(db) -> None:
    with db.connect() as connection:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE}(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_project_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL UNIQUE,
                alert_month_jump_pct REAL NOT NULL DEFAULT 35,
                alert_contractor_concentration_pct REAL NOT NULL DEFAULT 50,
                alert_unlinked_boq_pct REAL NOT NULL DEFAULT 10,
                approved_due_days INTEGER NOT NULL DEFAULT 30,
                updated_by TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
            """
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_cashflow_v3_settings_master "
            f"ON {SETTINGS_TABLE}(master_project_id,workspace_project_id)"
        )


def get_settings(db, workspace_project_id: int) -> dict[str, Any]:
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    ensure_schema(db)
    pid = int(workspace_project_id)
    scope = v1._resolve_scope(db, pid)
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT * FROM {SETTINGS_TABLE} WHERE workspace_project_id=? LIMIT 1", (pid,)
        ).fetchone()
        if not row:
            stamp = v1._now()
            connection.execute(
                f"""INSERT INTO {SETTINGS_TABLE}(
                       master_project_id,workspace_project_id,alert_month_jump_pct,
                       alert_contractor_concentration_pct,alert_unlinked_boq_pct,
                       approved_due_days,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    int(scope["master_project_id"]), pid,
                    DEFAULTS["alert_month_jump_pct"],
                    DEFAULTS["alert_contractor_concentration_pct"],
                    DEFAULTS["alert_unlinked_boq_pct"],
                    int(DEFAULTS["approved_due_days"]), stamp, stamp,
                ),
            )
            row = connection.execute(
                f"SELECT * FROM {SETTINGS_TABLE} WHERE workspace_project_id=? LIMIT 1", (pid,)
            ).fetchone()
    out = _rowdict(row)
    for key, value in DEFAULTS.items():
        out.setdefault(key, value)
    return out


def save_settings(db, workspace_project_id: int, values: dict[str, Any], *, actor: Any = None) -> None:
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    current = get_settings(db, int(workspace_project_id))
    jump = max(0.0, min(500.0, _float(values.get("alert_month_jump_pct"), _float(current.get("alert_month_jump_pct"), 35))))
    conc = max(1.0, min(100.0, _float(values.get("alert_contractor_concentration_pct"), _float(current.get("alert_contractor_concentration_pct"), 50))))
    unlinked = max(0.0, min(100.0, _float(values.get("alert_unlinked_boq_pct"), _float(current.get("alert_unlinked_boq_pct"), 10))))
    due_days = max(1, min(365, _int(values.get("approved_due_days"), _int(current.get("approved_due_days"), 30))))
    with db.connect() as connection:
        connection.execute(
            f"""UPDATE {SETTINGS_TABLE}
                SET alert_month_jump_pct=?,alert_contractor_concentration_pct=?,
                    alert_unlinked_boq_pct=?,approved_due_days=?,updated_by=?,updated_at=?
                WHERE workspace_project_id=?""",
            (jump, conc, unlinked, due_days, _actor_name(actor), v1._now(), int(workspace_project_id)),
        )


def _in_window(row: dict[str, Any], start: date, end: date) -> bool:
    d = _parse_date(row.get("forecast_date"))
    return bool(d and start <= d <= end)


def summarize_rows(rows: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    selected = [r for r in rows if _in_window(r, start, end)]
    return {
        "rows": len(selected),
        "plan": sum(max(0.0, _float(r.get("planned_amount"))) for r in selected),
        "forecast": sum(max(0.0, _float(r.get("outstanding"))) for r in selected),
        "expected": sum(max(0.0, _float(r.get("expected_amount"))) for r in selected),
        "approved_waiting": sum(
            max(0.0, _float(r.get("outstanding"))) for r in selected
            if "duyet" in _text(r.get("probability_reason")).lower() or _float(r.get("base_probability")) >= 0.999
        ),
    }


def scenario_summary(db, workspace_project_id: int, start: date, end: date) -> list[dict[str, Any]]:
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    out: list[dict[str, Any]] = []
    for scenario in v1.SCENARIOS:
        rows = v1.build_forecast_rows(db, int(workspace_project_id), scenario=scenario)
        summary = summarize_rows(rows, start, end)
        out.append({"scenario": scenario, **summary})
    return out


def monthly_expected(rows: list[dict[str, Any]], start: date, end: date, *, shift_days: int = 0,
                     contractor: str = "") -> list[dict[str, Any]]:
    buckets: dict[str, float] = {}
    contractor = _text(contractor)
    for row in rows:
        d = _parse_date(row.get("forecast_date"))
        if not d:
            continue
        if shift_days > 0 and _text(row.get("source_type")) == "SCHEDULE_BOQ":
            if not contractor or contractor == _text(row.get("contractor")):
                d = d + timedelta(days=int(shift_days))
        if not (start <= d <= end):
            continue
        key = d.strftime("%Y-%m")
        buckets[key] = buckets.get(key, 0.0) + max(0.0, _float(row.get("expected_amount")))
    return [{"month": key, "expected": buckets[key]} for key in sorted(buckets)]


def build_alerts(db, workspace_project_id: int, rows: list[dict[str, Any]], diagnostics: dict[str, Any],
                 start: date, end: date) -> list[dict[str, Any]]:
    cfg = get_settings(db, int(workspace_project_id))
    today = date.today()
    alerts: list[dict[str, Any]] = []

    for row in rows:
        outstanding = max(0.0, _float(row.get("outstanding")))
        if outstanding <= 0:
            continue
        overdue = _int(row.get("overdue_days"))
        if overdue > 0:
            alerts.append({
                "severity": "CRITICAL" if overdue >= 15 else "WARNING",
                "code": "OVERDUE",
                "title": f"{row.get('claim_code') or row.get('source_key')} quá hạn {overdue} ngày",
                "amount": outstanding,
                "detail": f"Còn phải trả {_money(outstanding)}; nhà thầu {_text(row.get('contractor')) or 'chưa xác định'}.",
            })
            continue
        due = _parse_date(row.get("forecast_date"))
        approved = _float(row.get("base_probability")) >= 0.999 or "duyet" in _text(row.get("probability_reason")).lower()
        if approved and due and today <= due <= today + timedelta(days=_int(cfg.get("approved_due_days"), 30)):
            alerts.append({
                "severity": "WARNING", "code": "APPROVED_DUE",
                "title": f"{row.get('claim_code') or row.get('source_key')} đã duyệt, sắp đến hạn",
                "amount": outstanding,
                "detail": f"Ngày forecast {due:%d/%m/%Y}; cần chuẩn bị {_money(outstanding)}.",
            })

    monthly = monthly_expected(rows, start, end)
    vals = [r["expected"] for r in monthly if r["expected"] > 0]
    if vals:
        avg = sum(vals) / len(vals)
        threshold = avg * (1.0 + _float(cfg.get("alert_month_jump_pct"), 35.0) / 100.0)
        for item in monthly:
            if item["expected"] > threshold and avg > 0:
                alerts.append({
                    "severity": "WARNING", "code": "MONTH_SPIKE",
                    "title": f"Áp lực dòng tiền cao tại {item['month']}",
                    "amount": item["expected"],
                    "detail": f"Expected {_money(item['expected'])}, cao hơn mức bình quân {_money(avg)}.",
                })

    scoped = [r for r in rows if _in_window(r, start, end)]
    by_contractor: dict[str, float] = {}
    total_expected = 0.0
    for row in scoped:
        value = max(0.0, _float(row.get("expected_amount")))
        total_expected += value
        name = _text(row.get("contractor")) or "Chưa xác định"
        by_contractor[name] = by_contractor.get(name, 0.0) + value
    if total_expected > 0 and by_contractor:
        name, value = max(by_contractor.items(), key=lambda kv: kv[1])
        share = value * 100.0 / total_expected
        if share >= _float(cfg.get("alert_contractor_concentration_pct"), 50.0):
            alerts.append({
                "severity": "INFO", "code": "CONCENTRATION",
                "title": f"Dòng tiền tập trung vào {name}", "amount": value,
                "detail": f"Chiếm {share:.1f}% Expected cash của kỳ forecast.",
            })

    unlinked = max(0.0, _float(diagnostics.get("unlinked_value")))
    linked_value = sum(max(0.0, _float(r.get("budget_total"))) for r in diagnostics.get("task_rows") or [])
    denom = unlinked + linked_value
    if denom > 0:
        pct = unlinked * 100.0 / denom
        if pct >= _float(cfg.get("alert_unlinked_boq_pct"), 10.0):
            alerts.append({
                "severity": "WARNING", "code": "UNLINKED_BOQ",
                "title": "BOQ chưa liên kết tiến độ làm giảm độ tin cậy forecast",
                "amount": unlinked,
                "detail": f"Giá trị chưa liên kết {_money(unlinked)} ({pct:.1f}% phần BOQ được kiểm tra).",
            })

    for item in diagnostics.get("task_rows") or []:
        planned = _parse_date(item.get("planned_finish"))
        projected = _parse_date(item.get("projected_finish"))
        if planned and projected and projected > planned:
            delay = (projected - planned).days
            if delay >= 15:
                alerts.append({
                    "severity": "WARNING", "code": "SCHEDULE_DELAY",
                    "title": f"{item.get('task_name') or item.get('task_ref')} dự kiến trễ {delay} ngày",
                    "amount": max(0.0, _float(item.get("forecast_remaining"))),
                    "detail": f"Dòng tiền liên quan {_money(item.get('forecast_remaining'))} có thể dịch sang kỳ sau.",
                })

    order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    alerts.sort(key=lambda r: (order.get(_text(r.get("severity")), 9), -_float(r.get("amount"))))
    return alerts


def _render_scenarios(st, db, pid: int, start: date, end: date) -> None:
    import pandas as pd

    data = scenario_summary(db, pid, start, end)
    cols = st.columns(3)
    for col, item in zip(cols, data):
        col.metric(item["scenario"], _money(item["expected"]), delta=f"Forecast {_money(item['forecast'])}")
    frame = pd.DataFrame([
        {"Scenario": x["scenario"], "Plan": x["plan"], "Forecast": x["forecast"], "Expected": x["expected"]}
        for x in data
    ])
    st.dataframe(frame, hide_index=True, use_container_width=True)
    st.bar_chart(frame.set_index("Scenario")[["Expected"]], use_container_width=True)


def _render_alerts(st, alerts: list[dict[str, Any]]) -> None:
    import pandas as pd

    if not alerts:
        st.success("Không phát hiện cảnh báo dòng tiền theo các ngưỡng V3 hiện tại.")
        return
    critical = sum(1 for a in alerts if a["severity"] == "CRITICAL")
    warning = sum(1 for a in alerts if a["severity"] == "WARNING")
    info = sum(1 for a in alerts if a["severity"] == "INFO")
    c1, c2, c3 = st.columns(3)
    c1.metric("Critical", critical)
    c2.metric("Warning", warning)
    c3.metric("Thông tin", info)
    frame = pd.DataFrame([
        {"Mức": a["severity"], "Mã": a["code"], "Cảnh báo": a["title"], "Giá trị": a["amount"], "Chi tiết": a["detail"]}
        for a in alerts
    ])
    st.dataframe(frame, hide_index=True, use_container_width=True)


def _render_delay_simulation(st, rows: list[dict[str, Any]], start: date, end: date, pid: int) -> None:
    import pandas as pd

    st.caption("Mô phỏng chỉ dịch ngày forecast của nguồn BOQ + tiến độ; không sửa dữ liệu gốc và không đổi giá trị tiền.")
    schedule_rows = [r for r in rows if _text(r.get("source_type")) == "SCHEDULE_BOQ"]
    contractors = sorted({_text(r.get("contractor")) for r in schedule_rows if _text(r.get("contractor"))})
    c1, c2 = st.columns(2)
    delay = c1.selectbox("Giả định chậm tiến độ", [0, 15, 30, 45, 60, 90], index=2, format_func=lambda x: f"{x} ngày", key=f"cashflow_v3_delay_{pid}")
    options = ["Tất cả nhà thầu"] + contractors
    selected = c2.selectbox("Phạm vi mô phỏng", options, key=f"cashflow_v3_delay_contractor_{pid}")
    contractor = "" if selected == "Tất cả nhà thầu" else selected

    base = {r["month"]: r["expected"] for r in monthly_expected(rows, start, end)}
    shifted = {r["month"]: r["expected"] for r in monthly_expected(rows, start, end, shift_days=int(delay), contractor=contractor)}
    months = sorted(set(base) | set(shifted))
    frame = pd.DataFrame([
        {"Tháng": m, "Base": base.get(m, 0.0), f"Trễ {delay} ngày": shifted.get(m, 0.0), "Chênh lệch": shifted.get(m, 0.0) - base.get(m, 0.0)}
        for m in months
    ])
    if frame.empty:
        st.info("Không có dòng BOQ + tiến độ trong kỳ để mô phỏng.")
        return
    st.bar_chart(frame.set_index("Tháng")[["Base", f"Trễ {delay} ngày"]], use_container_width=True)
    st.dataframe(frame, hide_index=True, use_container_width=True)


def _render_settings_v3(st, db, pid: int, *, identity: Any, can_update: bool) -> None:
    cfg = get_settings(db, pid)
    st.markdown("#### Ngưỡng cảnh báo V3")
    with st.form(f"cashflow_v3_settings_{pid}"):
        c1, c2 = st.columns(2)
        jump = c1.number_input("Cảnh báo tháng tăng cao hơn bình quân (%)", min_value=0.0, max_value=500.0, value=_float(cfg.get("alert_month_jump_pct"), 35.0), step=5.0)
        conc = c2.number_input("Cảnh báo tập trung theo nhà thầu (%)", min_value=1.0, max_value=100.0, value=_float(cfg.get("alert_contractor_concentration_pct"), 50.0), step=5.0)
        c1, c2 = st.columns(2)
        unlinked = c1.number_input("Cảnh báo BOQ chưa liên kết tiến độ (%)", min_value=0.0, max_value=100.0, value=_float(cfg.get("alert_unlinked_boq_pct"), 10.0), step=5.0)
        due = c2.number_input("Khoảng cảnh báo Claim đã duyệt sắp đến hạn (ngày)", min_value=1, max_value=365, value=_int(cfg.get("approved_due_days"), 30), step=1)
        save = st.form_submit_button("💾 Lưu ngưỡng V3", disabled=not can_update, use_container_width=True)
        if save:
            save_settings(db, pid, {"alert_month_jump_pct": jump, "alert_contractor_concentration_pct": conc, "alert_unlinked_boq_pct": unlinked, "approved_due_days": due}, actor=identity)
            st.success("Đã lưu ngưỡng cảnh báo V3.")
            st.rerun()


def render_cashflow_forecast_v3(st, db, workspace_project_id: int, *, identity: Any = None,
                                can_update: bool = False, is_admin: bool = False) -> None:
    del is_admin
    import qlda.runtime_core.cashflow_forecast_v1 as v1
    import qlda.runtime_core.cashflow_forecast_v2 as v2

    pid = int(workspace_project_id)
    v1.ensure_schema(db)
    v2.ensure_schema(db)
    ensure_schema(db)

    with st.expander("💸 Dự trù dòng tiền V3 • AI + Scenario + Cảnh báo", expanded=True):
        st.caption(
            f"Phạm vi: {v1._scope_label(db, pid)}. V3 dùng dữ liệu V1 + V2; scenario và mô phỏng chỉ phục vụ phân tích, không ghi đè Claim/BOQ/tiến độ."
        )
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        horizon_label = c1.selectbox("Kỳ forecast", list(v1.HORIZONS.keys()), index=2, key=f"cashflow_v3_horizon_{pid}")
        scenario = c2.selectbox("Scenario", list(v1.SCENARIOS), index=0, key=f"cashflow_v3_scenario_{pid}")
        start = date.today()
        days = v1.HORIZONS[horizon_label]
        if days > 0:
            end = start + timedelta(days=days)
            c3.date_input("Đến ngày", value=end, disabled=True, key=f"cashflow_v3_end_view_{pid}")
        else:
            end = c3.date_input("Đến ngày tùy chọn", value=start + timedelta(days=365), min_value=start, max_value=start + timedelta(days=365), key=f"cashflow_v3_custom_end_{pid}")
            if end < start:
                end = start

        rows = v1.build_forecast_rows(db, pid, scenario=scenario)
        _, diagnostics = v2.build_schedule_boq_rows(db, pid, scenario=scenario)
        alerts = build_alerts(db, pid, rows, diagnostics, start, end)
        tabs = st.tabs(["Dashboard", "Scenario", "Cảnh báo", "Mô phỏng trễ", "Chi tiết forecast", "BOQ & tiến độ", "Giả định"])

        with tabs[0]:
            v1._render_dashboard(st, rows, start, end, scenario)
            s = summarize_rows(rows, start, end)
            c1, c2, c3 = st.columns(3)
            c1.metric("Expected kỳ chọn", _money(s["expected"]))
            c2.metric("Forecast kỳ chọn", _money(s["forecast"]))
            c3.metric("Cảnh báo", len(alerts))
        with tabs[1]:
            _render_scenarios(st, db, pid, start, end)
        with tabs[2]:
            _render_alerts(st, alerts)
        with tabs[3]:
            _render_delay_simulation(st, rows, start, end, pid)
        with tabs[4]:
            v2._render_detail_v2(st, db, pid, rows, identity=identity, can_update=can_update)
        with tabs[5]:
            v2._render_schedule_diagnostics(st, diagnostics)
        with tabs[6]:
            v2._render_settings_v2(st, db, pid, identity=identity, can_update=can_update)
            _render_settings_v3(st, db, pid, identity=identity, can_update=can_update)


def _register_postgres_table() -> None:
    try:
        import qlda.runtime_core.project_database as pg
        order = list(getattr(pg, "TABLE_ORDER", ()))
        if SETTINGS_TABLE not in order:
            order.append(SETTINGS_TABLE)
            pg.TABLE_ORDER = tuple(order)
            pg._ID_TABLES = set(pg.TABLE_ORDER)
    except Exception:
        pass


def install_cashflow_forecast_v3() -> None:
    """Upgrade the installed V2 cashflow panel to the V3 analytical UI."""
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    if getattr(v1, "_qlda_cashflow_forecast_v3_installed", False):
        return
    _register_postgres_table()
    v1.render_cashflow_forecast_v1 = render_cashflow_forecast_v3
    v1._qlda_cashflow_forecast_v3_installed = True
    v1._qlda_cashflow_forecast_v3_marker = PATCH_MARKER


__all__ = [
    "ensure_schema", "get_settings", "save_settings", "summarize_rows", "scenario_summary",
    "monthly_expected", "build_alerts", "render_cashflow_forecast_v3", "install_cashflow_forecast_v3",
]
