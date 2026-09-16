from __future__ import annotations

"""Project Cost Management cho QLDA.

Nguyên tắc:
- Hợp đồng/Phụ lục vẫn được quản lý duy nhất trong Hồ sơ Hợp đồng.
- Quản lý Tài chính chỉ đọc lại các hồ sơ đó để tính Committed Cost, không tạo bản sao.
- VO đã duyệt được trình bày riêng, không tự cộng vào Committed Cost để tránh cộng trùng
  khi VO đã được hợp thức hóa bằng Phụ lục hợp đồng.
- AC (Actual Cost) là chi phí thực tế phát sinh, không đồng nhất với tiền đã thanh toán;
  vì vậy AC được ghi nhận riêng theo ngày kiểm soát để tính EVM.
"""

from datetime import date, datetime
from functools import wraps
import inspect
from typing import Any

from qlda.runtime_core.finance_common import (
    actual_progress as _actual_progress,
    planned_progress as _planned_progress,
    resolve_scope as _shared_resolve_scope,
    task_ref as _task_ref,
)

PATCH_MARKER = "V7.6 PROJECT COST MANAGEMENT PMBOK V1"
SETTINGS_TABLE = "project_cost_settings"
SNAPSHOTS_TABLE = "project_cost_control_snapshots"


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


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _actor(identity: Any) -> str:
    row = _rowdict(identity)
    return _text(row.get("name") or row.get("email") or "Người dùng")


def _money(value: Any) -> str:
    number = _float(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000_000:
        return f"{sign}{number / 1_000_000_000:,.2f} tỷ"
    if number >= 1_000_000:
        return f"{sign}{number / 1_000_000:,.1f} triệu"
    return f"{sign}{number:,.0f} đ"


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value):.3f}"


def _table_exists(connection, table: str) -> bool:
    try:
        connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def _resolve_scope(db, pid: int) -> dict[str, Any]:
    return _shared_resolve_scope(db, int(pid))


def ensure_schema(db) -> None:
    with db.connect() as connection:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE}(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_project_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL UNIQUE,
                currency TEXT NOT NULL DEFAULT 'VND',
                baseline_work_cost REAL NOT NULL DEFAULT 0,
                contingency_reserve REAL NOT NULL DEFAULT 0,
                management_reserve REAL NOT NULL DEFAULT 0,
                estimate_tolerance_pct REAL NOT NULL DEFAULT 5,
                control_threshold_pct REAL NOT NULL DEFAULT 10,
                baseline_date TEXT DEFAULT '',
                note TEXT DEFAULT '',
                updated_by TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
            """
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_project_cost_settings_master ON {SETTINGS_TABLE}(master_project_id,workspace_project_id)"
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SNAPSHOTS_TABLE}(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_project_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL,
                status_date TEXT NOT NULL,
                actual_cost REAL NOT NULL DEFAULT 0,
                note TEXT DEFAULT '',
                updated_by TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                UNIQUE(workspace_project_id,status_date)
            )
            """
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_project_cost_snapshots_workspace ON {SNAPSHOTS_TABLE}(workspace_project_id,status_date,id)"
        )


def _boq_total(db, pid: int) -> float:
    with db.connect() as connection:
        if not _table_exists(connection, "cost_budgets"):
            return 0.0
        try:
            row = connection.execute(
                "SELECT COALESCE(SUM(budget_total),0) AS total FROM cost_budgets WHERE project_id=?", (int(pid),)
            ).fetchone()
            return max(0.0, _float(_rowdict(row).get("total") if row else 0))
        except Exception:
            rows = connection.execute("SELECT * FROM cost_budgets WHERE project_id=?", (int(pid),)).fetchall()
            return sum(max(0.0, _float(_rowdict(r).get("budget_total"))) for r in rows)


def get_settings(db, pid: int) -> dict[str, Any]:
    ensure_schema(db)
    pid = int(pid)
    scope = _resolve_scope(db, pid)
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT * FROM {SETTINGS_TABLE} WHERE workspace_project_id=? LIMIT 1", (pid,)
        ).fetchone()
        if not row:
            stamp = _now()
            boq = _boq_total(db, pid)
            connection.execute(
                f"""INSERT INTO {SETTINGS_TABLE}(
                       master_project_id,workspace_project_id,currency,baseline_work_cost,
                       contingency_reserve,management_reserve,estimate_tolerance_pct,
                       control_threshold_pct,baseline_date,note,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(scope.get("master_project_id") or pid), pid, "VND", boq, 0.0, 0.0,
                    5.0, 10.0, date.today().isoformat(), "", stamp, stamp,
                ),
            )
            row = connection.execute(
                f"SELECT * FROM {SETTINGS_TABLE} WHERE workspace_project_id=? LIMIT 1", (pid,)
            ).fetchone()
    return _rowdict(row)


def save_settings(db, pid: int, values: dict[str, Any], *, actor: Any = None) -> None:
    current = get_settings(db, int(pid))
    with db.connect() as connection:
        connection.execute(
            f"""UPDATE {SETTINGS_TABLE}
                SET currency=?,baseline_work_cost=?,contingency_reserve=?,management_reserve=?,
                    estimate_tolerance_pct=?,control_threshold_pct=?,baseline_date=?,note=?,updated_by=?,updated_at=?
                WHERE workspace_project_id=?""",
            (
                _text(values.get("currency") or current.get("currency") or "VND").upper(),
                max(0.0, _float(values.get("baseline_work_cost"), current.get("baseline_work_cost"))),
                max(0.0, _float(values.get("contingency_reserve"), current.get("contingency_reserve"))),
                max(0.0, _float(values.get("management_reserve"), current.get("management_reserve"))),
                max(0.0, min(100.0, _float(values.get("estimate_tolerance_pct"), current.get("estimate_tolerance_pct")))),
                max(0.0, min(100.0, _float(values.get("control_threshold_pct"), current.get("control_threshold_pct")))),
                _text(values.get("baseline_date") or current.get("baseline_date") or date.today().isoformat())[:10],
                _text(values.get("note") if "note" in values else current.get("note")),
                _actor(actor), _now(), int(pid),
            ),
        )


def save_actual_cost(db, pid: int, status_date: date, actual_cost: float, *, note: str = "", actor: Any = None) -> None:
    ensure_schema(db)
    pid = int(pid)
    scope = _resolve_scope(db, pid)
    d = status_date.isoformat()
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT id FROM {SNAPSHOTS_TABLE} WHERE workspace_project_id=? AND status_date=? LIMIT 1", (pid, d)
        ).fetchone()
        stamp = _now()
        if row:
            connection.execute(
                f"UPDATE {SNAPSHOTS_TABLE} SET actual_cost=?,note=?,updated_by=?,updated_at=? WHERE workspace_project_id=? AND status_date=?",
                (max(0.0, _float(actual_cost)), _text(note), _actor(actor), stamp, pid, d),
            )
        else:
            connection.execute(
                f"""INSERT INTO {SNAPSHOTS_TABLE}(
                       master_project_id,workspace_project_id,status_date,actual_cost,note,updated_by,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    int(scope.get("master_project_id") or pid), pid, d, max(0.0, _float(actual_cost)),
                    _text(note), _actor(actor), stamp, stamp,
                ),
            )


def list_actual_cost_snapshots(db, pid: int) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM {SNAPSHOTS_TABLE} WHERE workspace_project_id=? ORDER BY status_date DESC,id DESC", (int(pid),)
        ).fetchall()
    return [_rowdict(r) for r in rows]


def _contract_summary(db, pid: int, currency: str) -> dict[str, Any]:
    try:
        from qlda.runtime_core.contract_management import list_contract_records
        records = list_contract_records(db, int(pid))
    except Exception:
        records = []
    currency = _text(currency).upper() or "VND"
    same = [r for r in records if _text(r.get("currency")).upper() == currency]
    contracts = sum(max(0.0, _float(r.get("amount"))) for r in same if _text(r.get("record_type")) == "Hợp đồng")
    appendices = sum(max(0.0, _float(r.get("amount"))) for r in same if _text(r.get("record_type")) == "Phụ lục")
    other_currency: dict[str, float] = {}
    for r in records:
        cur = _text(r.get("currency")).upper() or "VND"
        if cur != currency:
            other_currency[cur] = other_currency.get(cur, 0.0) + max(0.0, _float(r.get("amount")))
    return {
        "records": records,
        "contracts": contracts,
        "appendices": appendices,
        "committed": contracts + appendices,
        "other_currency": other_currency,
    }


def _vo_summary(db, pid: int) -> dict[str, float]:
    proposed = approved = 0.0
    with db.connect() as connection:
        if not _table_exists(connection, "cost_variations"):
            return {"proposed": 0.0, "approved": 0.0}
        try:
            rows = connection.execute("SELECT * FROM cost_variations WHERE project_id=?", (int(pid),)).fetchall()
        except Exception:
            rows = []
    for raw in rows:
        row = _rowdict(raw)
        proposed += max(0.0, _float(row.get("proposed_amount")))
        approved += max(0.0, _float(row.get("approved_amount")))
    return {"proposed": proposed, "approved": approved}


def _ipc_summary(db, pid: int) -> dict[str, float]:
    certified = 0.0
    paid_claim = 0.0
    paid_tracking = 0.0
    with db.connect() as connection:
        if _table_exists(connection, "payment_claims"):
            try:
                rows = connection.execute("SELECT * FROM payment_claims WHERE project_id=?", (int(pid),)).fetchall()
            except Exception:
                rows = []
            for raw in rows:
                row = _rowdict(raw)
                certified = max(certified, max(0.0, _float(row.get("certified_cumulative"))))
                paid_claim += max(0.0, _float(row.get("disbursed_amount")))
        if _table_exists(connection, "payment_tracking"):
            try:
                rows = connection.execute("SELECT * FROM payment_tracking WHERE project_id=?", (int(pid),)).fetchall()
            except Exception:
                rows = []
            paid_tracking = sum(max(0.0, _float(_rowdict(r).get("paid_amount"))) for r in rows)
    return {"certified": certified, "paid": max(paid_claim, paid_tracking)}


def _evm_progress(db, pid: int, on_date: date) -> dict[str, float]:
    with db.connect() as connection:
        try:
            budgets = [_rowdict(r) for r in connection.execute(
                "SELECT * FROM cost_budgets WHERE project_id=? ORDER BY id", (int(pid),)
            ).fetchall()]
        except Exception:
            budgets = []
        try:
            tasks = [_rowdict(r) for r in connection.execute(
                "SELECT * FROM tasks WHERE project_id=? AND COALESCE(is_summary,0)=0 ORDER BY id", (int(pid),)
            ).fetchall()]
        except Exception:
            tasks = []

    task_map: dict[str, dict[str, Any]] = {}
    for task in tasks:
        try:
            task_map[_task_ref(task)] = task
        except Exception:
            pass
        if _text(task.get("wbs")):
            task_map.setdefault(_text(task.get("wbs")), task)

    pv = ev = linked = unlinked = 0.0
    for row in budgets:
        budget = max(0.0, _float(row.get("budget_total")))
        task = task_map.get(_text(row.get("task_ref")))
        if not task:
            unlinked += budget
            continue
        linked += budget
        try:
            planned = _planned_progress(task, on_date)
            actual = _actual_progress(task)
        except Exception:
            planned = _float(task.get("planned_progress"))
            actual = _float(task.get("actual_progress"))
        pv += budget * max(0.0, min(100.0, planned)) / 100.0
        ev += budget * max(0.0, min(100.0, actual)) / 100.0
    return {"pv": pv, "ev": ev, "linked_boq": linked, "unlinked_boq": unlinked}


def build_cost_snapshot(db, pid: int, *, on_date: date | None = None) -> dict[str, Any]:
    pid = int(pid)
    settings = get_settings(db, pid)
    on_date = on_date or date.today()
    boq = _boq_total(db, pid)
    baseline_work = max(0.0, _float(settings.get("baseline_work_cost")))
    contingency = max(0.0, _float(settings.get("contingency_reserve")))
    management = max(0.0, _float(settings.get("management_reserve")))
    cost_baseline = baseline_work + contingency
    total_budget = cost_baseline + management
    contracts = _contract_summary(db, pid, _text(settings.get("currency")) or "VND")
    vo = _vo_summary(db, pid)
    ipc = _ipc_summary(db, pid)
    progress = _evm_progress(db, pid, on_date)
    snapshots = list_actual_cost_snapshots(db, pid)
    latest = snapshots[0] if snapshots else {}
    ac = max(0.0, _float(latest.get("actual_cost"))) if latest else None
    pv = progress["pv"]
    ev = progress["ev"]
    cv = (ev - ac) if ac is not None else None
    sv = ev - pv
    cpi = (ev / ac) if ac is not None and ac > 0 else None
    spi = (ev / pv) if pv > 0 else None
    eac = (cost_baseline / cpi) if cpi is not None and cpi > 0 and cost_baseline > 0 else None
    etc = (eac - ac) if eac is not None and ac is not None else None
    vac = (cost_baseline - eac) if eac is not None else None
    tcpi = None
    if ac is not None and (cost_baseline - ac) > 0:
        tcpi = (cost_baseline - ev) / (cost_baseline - ac)
    return {
        "settings": settings,
        "boq_estimate": boq,
        "baseline_work_cost": baseline_work,
        "contingency_reserve": contingency,
        "management_reserve": management,
        "cost_baseline": cost_baseline,
        "total_budget": total_budget,
        "contract_value": contracts["contracts"],
        "appendix_value": contracts["appendices"],
        "committed_cost": contracts["committed"],
        "contract_records": contracts["records"],
        "other_currency": contracts["other_currency"],
        "vo_proposed": vo["proposed"],
        "vo_approved": vo["approved"],
        "certified_cost": ipc["certified"],
        "paid_cash": ipc["paid"],
        "pv": pv,
        "ev": ev,
        "ac": ac,
        "ac_status_date": _text(latest.get("status_date")) if latest else "",
        "cv": cv,
        "sv": sv,
        "cpi": cpi,
        "spi": spi,
        "eac": eac,
        "etc": etc,
        "vac": vac,
        "tcpi": tcpi,
        "linked_boq": progress["linked_boq"],
        "unlinked_boq": progress["unlinked_boq"],
        "snapshots": snapshots,
    }


def render_budget_baseline(st, db, pid: int, *, identity: Any = None, can_update: bool = False) -> None:
    import pandas as pd

    snap = build_cost_snapshot(db, int(pid))
    settings = snap["settings"]
    st.markdown("### 📊 Ngân sách & Đường cơ sở chi phí")
    st.caption("Đường cơ sở chi phí = Chi phí công việc trong baseline + Dự phòng rủi ro. Dự phòng quản lý nằm ngoài Cost Baseline.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BOQ hiện tại", _money(snap["boq_estimate"]))
    c2.metric("Chi phí công việc baseline", _money(snap["baseline_work_cost"]))
    c3.metric("Dự phòng rủi ro", _money(snap["contingency_reserve"]))
    c4.metric("Cost Baseline", _money(snap["cost_baseline"]))
    c1, c2, c3 = st.columns(3)
    c1.metric("Dự phòng quản lý", _money(snap["management_reserve"]))
    c2.metric("Tổng ngân sách dự án", _money(snap["total_budget"]))
    variance = snap["boq_estimate"] - snap["baseline_work_cost"]
    c3.metric("BOQ hiện tại - Baseline", _money(variance))

    with st.expander("⚙️ Thiết lập đường cơ sở chi phí", expanded=False):
        with st.form(f"project_cost_settings_{pid}"):
            c1, c2, c3 = st.columns(3)
            currency = c1.text_input("Tiền tệ", value=_text(settings.get("currency")) or "VND")
            baseline_work = c2.number_input("Chi phí công việc trong baseline", min_value=0.0, value=float(snap["baseline_work_cost"]), step=1_000_000.0)
            baseline_date = c3.date_input("Ngày chốt baseline", value=datetime.strptime((_text(settings.get("baseline_date")) or date.today().isoformat())[:10], "%Y-%m-%d").date())
            c1, c2 = st.columns(2)
            contingency = c1.number_input("Dự phòng rủi ro (Contingency)", min_value=0.0, value=float(snap["contingency_reserve"]), step=1_000_000.0)
            management = c2.number_input("Dự phòng quản lý (Management Reserve)", min_value=0.0, value=float(snap["management_reserve"]), step=1_000_000.0)
            c1, c2 = st.columns(2)
            tolerance = c1.number_input("Dung sai ước tính (%)", 0.0, 100.0, float(settings.get("estimate_tolerance_pct") or 5.0), 1.0)
            threshold = c2.number_input("Ngưỡng kiểm soát chi phí (%)", 0.0, 100.0, float(settings.get("control_threshold_pct") or 10.0), 1.0)
            note = st.text_area("Ghi chú", value=_text(settings.get("note")))
            if st.form_submit_button("💾 Lưu đường cơ sở chi phí", disabled=not can_update, use_container_width=True):
                save_settings(db, pid, {
                    "currency": currency, "baseline_work_cost": baseline_work,
                    "contingency_reserve": contingency, "management_reserve": management,
                    "estimate_tolerance_pct": tolerance, "control_threshold_pct": threshold,
                    "baseline_date": baseline_date.isoformat(), "note": note,
                }, actor=identity)
                st.success("Đã lưu đường cơ sở chi phí.")
                st.rerun()

    st.markdown("#### Tham chiếu cam kết chi phí từ Hồ sơ Hợp đồng")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Hợp đồng gốc", _money(snap["contract_value"]))
    c2.metric("Phụ lục", _money(snap["appendix_value"]))
    c3.metric("Committed Cost", _money(snap["committed_cost"]))
    c4.metric("VO đã duyệt (tham chiếu)", _money(snap["vo_approved"]))
    st.caption("Committed Cost chỉ đọc Hợp đồng + Phụ lục hiện có trong Hồ sơ. VO đã duyệt hiển thị riêng và không tự cộng để tránh cộng trùng khi VO đã được đưa vào Phụ lục.")

    records = [r for r in snap["contract_records"] if _text(r.get("currency")).upper() == (_text(settings.get("currency")).upper() or "VND")]
    if records:
        frame = pd.DataFrame([{
            "Loại": r.get("record_type", ""), "Số hồ sơ": r.get("record_no", ""),
            "Tiêu đề": r.get("title", ""), "Ngày ký": r.get("signed_date", ""),
            "Giá trị": r.get("amount", 0.0), "Tiền tệ": r.get("currency", ""),
        } for r in records])
        st.dataframe(frame, hide_index=True, use_container_width=True)
    if snap["other_currency"]:
        st.warning("Có hợp đồng/phụ lục khác tiền tệ baseline: " + ", ".join(f"{k}: {_money(v)}" for k, v in snap["other_currency"].items()) + ". Các giá trị này chưa được quy đổi/cộng vào Committed Cost.")


def render_cost_control(st, db, pid: int, *, identity: Any = None, can_update: bool = False) -> None:
    import pandas as pd

    snap = build_cost_snapshot(db, int(pid))
    st.markdown("### 📈 Kiểm soát chi phí")
    st.caption("EVM dùng PV/EV từ BOQ liên kết tiến độ. AC phải là chi phí thực tế phát sinh; không dùng số tiền đã thanh toán thay cho AC.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BAC / Cost Baseline", _money(snap["cost_baseline"]))
    c2.metric("Committed Cost", _money(snap["committed_cost"]))
    c3.metric("Giá trị nghiệm thu lũy kế", _money(snap["certified_cost"]))
    c4.metric("Đã thanh toán", _money(snap["paid_cash"]))

    c1, c2, c3 = st.columns(3)
    c1.metric("PV - Giá trị kế hoạch", _money(snap["pv"]))
    c2.metric("EV - Giá trị thu được", _money(snap["ev"]))
    c3.metric("AC - Chi phí thực tế", _money(snap["ac"]) if snap["ac"] is not None else "Chưa nhập")

    metrics = [
        {"Chỉ số": "CV = EV - AC", "Giá trị": _money(snap["cv"]) if snap["cv"] is not None else "Chưa có AC", "Ý nghĩa": "Chênh lệch chi phí"},
        {"Chỉ số": "SV = EV - PV", "Giá trị": _money(snap["sv"]), "Ý nghĩa": "Chênh lệch tiến độ theo giá trị"},
        {"Chỉ số": "CPI = EV / AC", "Giá trị": _pct(snap["cpi"]), "Ý nghĩa": "Hiệu quả chi phí"},
        {"Chỉ số": "SPI = EV / PV", "Giá trị": _pct(snap["spi"]), "Ý nghĩa": "Hiệu quả tiến độ"},
        {"Chỉ số": "EAC = BAC / CPI", "Giá trị": _money(snap["eac"]) if snap["eac"] is not None else "Chưa đủ dữ liệu", "Ý nghĩa": "Ước tính chi phí khi hoàn thành"},
        {"Chỉ số": "ETC = EAC - AC", "Giá trị": _money(snap["etc"]) if snap["etc"] is not None else "Chưa đủ dữ liệu", "Ý nghĩa": "Chi phí còn cần để hoàn thành"},
        {"Chỉ số": "VAC = BAC - EAC", "Giá trị": _money(snap["vac"]) if snap["vac"] is not None else "Chưa đủ dữ liệu", "Ý nghĩa": "Chênh lệch ngân sách dự kiến"},
        {"Chỉ số": "TCPI", "Giá trị": _pct(snap["tcpi"]), "Ý nghĩa": "Hiệu suất cần đạt cho phần còn lại"},
    ]
    st.dataframe(pd.DataFrame(metrics), hide_index=True, use_container_width=True)

    threshold = _float(snap["settings"].get("control_threshold_pct"), 10.0)
    warnings = []
    if snap["cost_baseline"] > 0 and snap["committed_cost"] > snap["cost_baseline"]:
        warnings.append(f"Committed Cost vượt Cost Baseline {_money(snap['committed_cost'] - snap['cost_baseline'])}.")
    if snap["cv"] is not None and snap["cost_baseline"] > 0:
        cv_pct = abs(_float(snap["cv"])) * 100.0 / snap["cost_baseline"]
        if cv_pct >= threshold:
            warnings.append(f"Biến động CV đạt {cv_pct:.1f}%, vượt ngưỡng kiểm soát {threshold:.1f}%.")
    if snap["unlinked_boq"] > 0:
        warnings.append(f"BOQ trị giá {_money(snap['unlinked_boq'])} chưa liên kết công việc nên chưa tham gia PV/EV.")
    if warnings:
        st.warning("⚠️ " + "\n\n".join(warnings))

    with st.expander("🧾 Cập nhật Actual Cost (AC)", expanded=False):
        latest_date = date.today()
        latest_ac = float(snap["ac"] or 0.0)
        with st.form(f"project_cost_ac_{pid}"):
            c1, c2 = st.columns(2)
            status_date = c1.date_input("Ngày kiểm soát", value=latest_date)
            actual_cost = c2.number_input("Chi phí thực tế phát sinh (AC)", min_value=0.0, value=latest_ac, step=1_000_000.0)
            note = st.text_area("Ghi chú nguồn AC", value="")
            if st.form_submit_button("💾 Lưu AC", disabled=not can_update, use_container_width=True):
                save_actual_cost(db, pid, status_date, actual_cost, note=note, actor=identity)
                st.success("Đã lưu Actual Cost.")
                st.rerun()
        if snap["snapshots"]:
            history = pd.DataFrame([{
                "Ngày kiểm soát": r.get("status_date", ""), "AC": r.get("actual_cost", 0.0),
                "Ghi chú": r.get("note", ""), "Cập nhật bởi": r.get("updated_by", ""),
            } for r in snap["snapshots"][:24]])
            st.dataframe(history, hide_index=True, use_container_width=True)

    st.markdown("#### Phân lớp chi phí")
    layers = pd.DataFrame([
        {"Lớp": "Ngân sách / Cost Baseline", "Giá trị": snap["cost_baseline"], "Nguồn": "Đường cơ sở được duyệt"},
        {"Lớp": "Committed Cost", "Giá trị": snap["committed_cost"], "Nguồn": "Hợp đồng + Phụ lục trong Hồ sơ"},
        {"Lớp": "VO đã duyệt", "Giá trị": snap["vo_approved"], "Nguồn": "Chi phí phát sinh (không tự cộng vào Committed)"},
        {"Lớp": "Certified Cost", "Giá trị": snap["certified_cost"], "Nguồn": "IPC nghiệm thu/chứng nhận lũy kế"},
        {"Lớp": "Paid Cash", "Giá trị": snap["paid_cash"], "Nguồn": "Thanh toán & giải ngân"},
    ])
    st.dataframe(layers, hide_index=True, use_container_width=True)


def _register_postgres_tables() -> None:
    try:
        import qlda.runtime_core.project_database as pg
        order = list(getattr(pg, "TABLE_ORDER", ()))
        changed = False
        for table in (SETTINGS_TABLE, SNAPSHOTS_TABLE):
            if table not in order:
                order.append(table)
                changed = True
        if changed:
            pg.TABLE_ORDER = tuple(order)
            pg._ID_TABLES = set(pg.TABLE_ORDER)
    except Exception:
        pass


def install_project_cost_management() -> None:
    """Thêm 2 sheet PMBOK vào Quản lý Tài chính mà không tạo sheet Hợp đồng mới."""
    import streamlit as st

    if getattr(st, "_qlda_project_cost_management_installed", False):
        return
    _register_postgres_tables()
    previous_tabs = st.tabs

    @wraps(previous_tabs)
    def tabs_with_cost_management(labels, *args, **kwargs):
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

            all_tabs = previous_tabs([
                "Chi phí dự toán (BOQ)",
                "Ngân sách & đường cơ sở",
                "Thanh toán & giải ngân",
                "Chi phí phát sinh (VO)",
                "Dự trù dòng tiền",
                "Kiểm soát chi phí",
            ], *args, **kwargs)

            glb, loc = caller.f_globals, caller.f_locals
            db = glb.get("db")
            pid = _int(loc.get("pid"))
            if db is not None and pid > 0:
                can_update_fn = glb.get("_can_update")
                is_admin_fn = glb.get("_is_admin")
                identity_fn = glb.get("_cloud_identity")
                can_update = bool(can_update_fn()) if callable(can_update_fn) else False
                identity = identity_fn() if callable(identity_fn) else None
                with all_tabs[1]:
                    render_budget_baseline(st, db, pid, identity=identity, can_update=can_update)
                with all_tabs[4]:
                    from qlda.runtime_core.finance_management_ui import render_cashflow_finance_sheet
                    render_cashflow_finance_sheet(
                        st, db, pid, identity=identity, can_update=can_update,
                        is_admin=bool(is_admin_fn()) if callable(is_admin_fn) else False,
                    )
                with all_tabs[5]:
                    render_cost_control(st, db, pid, identity=identity, can_update=can_update)

            # app.py vẫn chỉ biết ba tab cũ; ánh xạ chúng về đúng vị trí mới.
            return (all_tabs[0], all_tabs[2], all_tabs[3])
        finally:
            del caller
            del frame

    st.tabs = tabs_with_cost_management
    st._qlda_project_cost_management_installed = True
    st._qlda_project_cost_management_marker = PATCH_MARKER


__all__ = [
    "ensure_schema", "get_settings", "save_settings", "save_actual_cost",
    "list_actual_cost_snapshots", "build_cost_snapshot", "render_budget_baseline",
    "render_cost_control", "install_project_cost_management",
]
