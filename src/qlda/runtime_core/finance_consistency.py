from __future__ import annotations

"""Consolidated finance consistency policy for QLDA Cleanup V2.3.

This module replaces four historical patch implementations with one source of
truth for:
- after-tax BOQ/BAC and legacy baseline migration;
- signed contract appendices and VO decreases;
- authoritative VO proposal/approval values across VO, reports, cost control and AI;
- simplified Budget/Baseline KPI presentation;
- the legacy BOQ BAC card and executive adjusted-budget card.

Compatibility facades keep old imports working, but all implementation lives here.
"""

from datetime import date, datetime
from functools import wraps
import inspect
import math
from typing import Any

PATCH_MARKER = "V7.6 CLEANUP V2.3 CONSOLIDATED FINANCE CONSISTENCY"


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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _close_money(a: Any, b: Any) -> bool:
    left = _num(a)
    right = _num(b)
    tolerance = max(1.0, abs(right) * 1e-9)
    return abs(left - right) <= tolerance


def _compact_money(value: Any, *, signed: bool = False) -> str:
    number = _num(value)
    sign = ""
    if signed:
        sign = "+" if number > 0 else ("-" if number < 0 else "")
    elif number < 0:
        sign = "-"
    number = abs(number)
    if number >= 1_000_000_000:
        return f"{sign}{number / 1_000_000_000:,.2f} tỷ"
    if number >= 1_000_000:
        return f"{sign}{number / 1_000_000:,.1f} triệu"
    return f"{sign}{number:,.0f} đ"


def saved_after_tax_total(db, project_id: int) -> float | None:
    try:
        from qlda.runtime_core.boq_persistence import load_saved_boq_workbook
        result = load_saved_boq_workbook(db, int(project_id))
    except Exception:
        result = None
    if not isinstance(result, dict):
        return None
    for key in ("after_tax_total", "grand_total"):
        raw = result.get(key)
        if raw in (None, ""):
            continue
        value = _num(raw, -1.0)
        if value >= 0:
            return value
    return None


def detail_boq_total(db, project_id: int) -> float:
    pid = int(project_id)
    try:
        with db.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(budget_total),0) AS total FROM cost_budgets WHERE project_id=?",
                (pid,),
            ).fetchone()
            if row is not None:
                data = _rowdict(row)
                if "total" in data:
                    return max(0.0, _num(data.get("total")))
                try:
                    return max(0.0, _num(row[0]))
                except Exception:
                    pass
    except Exception:
        pass
    try:
        return sum(max(0.0, _num(r["budget_total"])) for r in db.cost_budgets(pid))
    except Exception:
        return 0.0


def boq_budget_total(db, project_id: int) -> float:
    after_tax = saved_after_tax_total(db, int(project_id))
    if after_tax is not None:
        return max(0.0, float(after_tax))
    return detail_boq_total(db, int(project_id))


def _migrate_legacy_baseline(db, project_id: int, settings: dict[str, Any]) -> dict[str, Any]:
    pid = int(project_id)
    after_tax = saved_after_tax_total(db, pid)
    if after_tax is None or after_tax <= 0:
        return settings
    detail_total = detail_boq_total(db, pid)
    baseline = _num(settings.get("baseline_work_cost"))
    if detail_total <= 0 or _close_money(after_tax, detail_total):
        return settings
    if not _close_money(baseline, detail_total):
        return settings
    try:
        with db.connect() as connection:
            connection.execute(
                "UPDATE project_cost_settings SET baseline_work_cost=?,updated_at=? WHERE workspace_project_id=?",
                (float(after_tax), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pid),
            )
    except Exception:
        pass
    migrated = dict(settings)
    migrated["baseline_work_cost"] = float(after_tax)
    migrated["_qlda_after_tax_baseline_migrated"] = True
    return migrated


def effective_proposed_value(row: dict[str, Any]) -> float:
    increase = _num(row.get("increase_amount"))
    decrease = _num(row.get("decrease_amount"))
    if abs(increase) + abs(decrease) >= 0.5:
        return increase + decrease
    return _num(row.get("proposed_amount"))


def _normalize_vo_connection(connection, project_id: int) -> int:
    try:
        rows = connection.execute(
            """SELECT vo_id,increase_amount,decrease_amount,proposed_amount
               FROM variation_orders WHERE project_id=?""",
            (int(project_id),),
        ).fetchall()
    except Exception:
        return 0
    changed = 0
    for raw in rows:
        row = _rowdict(raw)
        increase = _num(row.get("increase_amount"))
        decrease = _num(row.get("decrease_amount"))
        if abs(increase) + abs(decrease) < 0.5:
            continue
        proposed = increase + decrease
        if abs(proposed - _num(row.get("proposed_amount"))) < 0.5:
            continue
        connection.execute(
            "UPDATE variation_orders SET proposed_amount=? WHERE vo_id=?",
            (float(proposed), str(row.get("vo_id") or "")),
        )
        changed += 1
    return changed


def normalize_project_vo_values(db, project_id: int) -> int:
    try:
        with db.connect() as connection:
            return _normalize_vo_connection(connection, int(project_id))
    except Exception:
        return 0


def independent_vo_rows(connection, project_id: int) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(
            """SELECT vo_code,'' AS task_ref,filename AS description,
                      increase_amount,decrease_amount,proposed_amount,approved_amount,
                      funding_source,status,vo_date,note
               FROM variation_orders WHERE project_id=? ORDER BY vo_no""",
            (int(project_id),),
        ).fetchall()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = _rowdict(raw)
        row["proposed_amount"] = effective_proposed_value(row)
        out.append(row)
    return out


def vo_summary(db, project_id: int) -> dict[str, float]:
    pid = int(project_id)
    try:
        normalize_project_vo_values(db, pid)
        with db.connect() as connection:
            rows = independent_vo_rows(connection, pid)
        if rows:
            return {
                "proposed": sum(effective_proposed_value(row) for row in rows),
                "approved": sum(
                    _num(row.get("approved_amount"))
                    for row in rows
                    if _text(row.get("status")) == "Đã duyệt"
                ),
            }
    except Exception:
        pass
    proposed = approved = 0.0
    try:
        with db.connect() as connection:
            rows = connection.execute(
                "SELECT proposed_amount,approved_amount FROM cost_variations WHERE project_id=?",
                (pid,),
            ).fetchall()
        for raw in rows:
            row = _rowdict(raw)
            proposed += _num(row.get("proposed_amount"))
            approved += _num(row.get("approved_amount"))
    except Exception:
        pass
    return {"proposed": proposed, "approved": approved}


def _signed_contract_summary(db, pid: int, currency: str) -> dict[str, Any]:
    try:
        from qlda.runtime_core.contract_management import list_contract_records
        records = list_contract_records(db, int(pid))
    except Exception:
        records = []
    currency_code = _text(currency).upper() or "VND"
    same = [r for r in records if (_text(r.get("currency")).upper() or "VND") == currency_code]
    contracts = sum(
        max(0.0, _num(r.get("amount")))
        for r in same
        if _text(r.get("record_type")) == "Hợp đồng"
    )
    appendices = sum(
        _num(r.get("amount"))
        for r in same
        if _text(r.get("record_type")) == "Phụ lục"
    )
    other_currency: dict[str, float] = {}
    for row in records:
        cur = _text(row.get("currency")).upper() or "VND"
        if cur != currency_code:
            other_currency[cur] = other_currency.get(cur, 0.0) + _num(row.get("amount"))
    return {
        "records": records,
        "contracts": contracts,
        "appendices": appendices,
        "committed": contracts + appendices,
        "other_currency": other_currency,
    }


def install_finance_consistency_core() -> None:
    import qlda.runtime_core.project_cost_management as pcm
    if getattr(pcm, "_qlda_finance_consistency_v23_core_installed", False):
        return
    original_get_settings = pcm.get_settings
    original_evm_progress = pcm._evm_progress
    pcm._boq_total = boq_budget_total
    pcm._contract_summary = _signed_contract_summary
    pcm._vo_summary = vo_summary

    @wraps(original_get_settings)
    def get_settings_consistent(db, pid: int):
        settings = original_get_settings(db, int(pid))
        try:
            return _migrate_legacy_baseline(db, int(pid), dict(settings or {}))
        except Exception:
            return settings

    @wraps(original_evm_progress)
    def evm_progress_consistent(db, pid: int, on_date):
        progress = dict(original_evm_progress(db, int(pid), on_date) or {})
        after_tax = saved_after_tax_total(db, int(pid))
        detail_total = detail_boq_total(db, int(pid))
        if after_tax is None or after_tax <= 0 or detail_total <= 0:
            return progress
        ratio = float(after_tax) / float(detail_total)
        if not math.isfinite(ratio) or ratio <= 0 or abs(ratio - 1.0) < 1e-12:
            return progress
        for key in ("pv", "ev", "linked_boq", "unlinked_boq"):
            progress[key] = max(0.0, _num(progress.get(key))) * ratio
        progress["tax_basis_ratio"] = ratio
        return progress

    pcm.get_settings = get_settings_consistent
    pcm._evm_progress = evm_progress_consistent

    try:
        import qlda.runtime_core.vo_claim as core
        if not getattr(core, "_qlda_finance_consistency_v23_save_installed", False):
            original_save = core.save_vo
            @wraps(original_save)
            def save_vo_consistent(db, project_id: int, result: dict[str, Any]):
                saved = original_save(db, int(project_id), result)
                normalize_project_vo_values(db, int(project_id))
                try:
                    code = _text(saved.get("vo_code") or result.get("vo_code"))
                    for row in core.list_vos(db, int(project_id)):
                        if _text(row.get("vo_code")) == code:
                            return row
                except Exception:
                    pass
                return saved
            core.save_vo = save_vo_consistent
            core._qlda_finance_consistency_v23_save_installed = True
    except Exception:
        pass

    try:
        import qlda.runtime_core.vo_independent as vo_ui
        if not getattr(vo_ui, "_qlda_finance_consistency_v23_ui_installed", False):
            original_render_vo = vo_ui.render_vo_ui
            @wraps(original_render_vo)
            def render_vo_ui_consistent(db, project_id: int, can_update: bool = True):
                normalize_project_vo_values(db, int(project_id))
                return original_render_vo(db, int(project_id), can_update=bool(can_update))
            vo_ui.render_vo_ui = render_vo_ui_consistent
            vo_ui._proposed_value = effective_proposed_value
            vo_ui._qlda_finance_consistency_v23_ui_installed = True
    except Exception:
        pass

    try:
        import qlda.runtime_core.report_cost as report_cost
        if not getattr(report_cost, "_qlda_finance_consistency_v23_installed", False):
            original_load_vo_rows = report_cost._load_vo_rows
            @wraps(original_load_vo_rows)
            def load_vo_rows_consistent(connection, project_id: int):
                rows = independent_vo_rows(connection, int(project_id))
                if rows:
                    return rows
                return original_load_vo_rows(connection, int(project_id))
            report_cost._load_vo_rows = load_vo_rows_consistent
            report_cost._qlda_finance_consistency_v23_installed = True
    except Exception:
        pass


    pcm._qlda_finance_consistency_v23_core_installed = True
    pcm._qlda_finance_consistency_v23_marker = PATCH_MARKER


def _render_budget_baseline_simplified(st, db, pid: int, *, identity: Any = None, can_update: bool = False) -> None:
    import qlda.runtime_core.project_cost_management as pcm
    snap = pcm.build_cost_snapshot(db, int(pid))
    settings = snap["settings"]
    st.markdown("### 📊 Ngân sách & Đường cơ sở chi phí")
    st.caption(
        "Đường cơ sở chi phí gồm chi phí công việc trong baseline và dự phòng rủi ro; "
        "dự phòng quản lý nằm ngoài đường cơ sở chi phí."
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("BOQ hiện tại", pcm._money(snap["boq_estimate"]))
    c2.metric("Đường cơ sở chi phí", pcm._money(snap["cost_baseline"]))
    c3.metric("Tổng ngân sách dự án", pcm._money(snap["total_budget"]))
    with st.expander("⚙️ Thiết lập đường cơ sở chi phí", expanded=False):
        with st.form(f"project_cost_settings_{pid}"):
            c1, c2, c3 = st.columns(3)
            currency = c1.text_input("Tiền tệ", value=pcm._text(settings.get("currency")) or "VND")
            baseline_work = c2.number_input(
                "Chi phí công việc trong baseline", min_value=0.0,
                value=float(snap["baseline_work_cost"]), step=1_000_000.0,
            )
            try:
                baseline_date_value = datetime.strptime(
                    (pcm._text(settings.get("baseline_date")) or date.today().isoformat())[:10], "%Y-%m-%d"
                ).date()
            except Exception:
                baseline_date_value = date.today()
            baseline_date = c3.date_input("Ngày chốt baseline", value=baseline_date_value)
            c1, c2 = st.columns(2)
            contingency = c1.number_input(
                "Dự phòng rủi ro", min_value=0.0,
                value=float(snap["contingency_reserve"]), step=1_000_000.0,
            )
            management = c2.number_input(
                "Dự phòng quản lý", min_value=0.0,
                value=float(snap["management_reserve"]), step=1_000_000.0,
            )
            c1, c2 = st.columns(2)
            tolerance = c1.number_input(
                "Dung sai ước tính (%)", 0.0, 100.0,
                float(settings.get("estimate_tolerance_pct") or 5.0), 1.0,
            )
            threshold = c2.number_input(
                "Ngưỡng kiểm soát chi phí (%)", 0.0, 100.0,
                float(settings.get("control_threshold_pct") or 10.0), 1.0,
            )
            note = st.text_area("Ghi chú", value=pcm._text(settings.get("note")))
            if st.form_submit_button(
                "💾 Lưu đường cơ sở chi phí", disabled=not can_update, use_container_width=True
            ):
                pcm.save_settings(
                    db, pid,
                    {
                        "currency": currency,
                        "baseline_work_cost": baseline_work,
                        "contingency_reserve": contingency,
                        "management_reserve": management,
                        "estimate_tolerance_pct": tolerance,
                        "control_threshold_pct": threshold,
                        "baseline_date": baseline_date.isoformat(),
                        "note": note,
                    },
                    actor=identity,
                )
                st.success("Đã lưu đường cơ sở chi phí.")
                st.rerun()


def _find_db_pid_from_stack() -> tuple[Any | None, int | None]:
    frame = inspect.currentframe()
    current = frame.f_back if frame else None
    try:
        for _ in range(16):
            if current is None:
                break
            loc = current.f_locals
            db = loc.get("db")
            pid = loc.get("pid", loc.get("project_id"))
            if db is not None and pid not in (None, ""):
                try:
                    return db, int(pid)
                except Exception:
                    pass
            current = current.f_back
    finally:
        del frame
        del current
    return None, None


def _overview_context() -> tuple[Any, int] | None:
    frame = inspect.currentframe()
    current = frame.f_back if frame else None
    try:
        for _ in range(16):
            if current is None:
                break
            if (
                current.f_code.co_name == "render_overview_v7"
                and current.f_globals.get("__name__") == "qlda.runtime_core.ui_v7_compact"
            ):
                db = current.f_locals.get("db")
                pid = current.f_locals.get("pid")
                if db is not None and pid not in (None, ""):
                    try:
                        return db, int(pid)
                    except Exception:
                        return None
            current = current.f_back
    finally:
        del frame
        del current
    return None


def _install_legacy_bac_metric() -> None:
    import streamlit as st
    if getattr(st, "_qlda_finance_consistency_v23_bac_metric", False):
        return
    original_metric = st.metric
    @wraps(original_metric)
    def metric_after_tax(label, value, *args, **kwargs):
        if _text(label) == "Tổng ngân sách kế hoạch (BAC)":
            db, pid = _find_db_pid_from_stack()
            if db is not None and pid is not None:
                total = saved_after_tax_total(db, pid)
                if total is not None:
                    label = "Tổng ngân sách kế hoạch sau thuế (BAC)"
                    value = f"{total:,.0f} VND"
        return original_metric(label, value, *args, **kwargs)
    st.metric = metric_after_tax
    st._qlda_finance_consistency_v23_bac_metric = True


def _install_overview_adjusted_budget_metric() -> None:
    try:
        from streamlit.delta_generator import DeltaGenerator
        import qlda.runtime_core.project_cost_management as pcm
    except Exception:
        return
    if getattr(DeltaGenerator, "_qlda_finance_consistency_v23_overview", False):
        return
    original_metric = DeltaGenerator.metric
    @wraps(original_metric)
    def metric_with_adjusted_budget(self, label, value, *args, **kwargs):
        if _text(label) == "BOQ":
            context = _overview_context()
            if context is not None:
                db, pid = context
                try:
                    snap = pcm.build_cost_snapshot(db, int(pid)) or {}
                    adjusted = max(0.0, _num(snap.get("committed_cost")))
                    boq = max(0.0, _num(snap.get("boq_estimate")))
                    label = "Ngân sách điều chỉnh"
                    value = _compact_money(adjusted)
                    if not args and "delta" not in kwargs:
                        kwargs["delta"] = f"{_compact_money(adjusted - boq, signed=True)} so BOQ"
                except Exception:
                    pass
        return original_metric(self, label, value, *args, **kwargs)
    DeltaGenerator.metric = metric_with_adjusted_budget
    DeltaGenerator._qlda_finance_consistency_v23_overview = True


def install_finance_consistency_ui() -> None:
    install_finance_consistency_core()
    import qlda.runtime_core.project_cost_management as pcm
    if not getattr(pcm, "_qlda_finance_consistency_v23_budget_ui", False):
        pcm.render_budget_baseline = _render_budget_baseline_simplified
        pcm._qlda_finance_consistency_v23_budget_ui = True
    _install_legacy_bac_metric()
    _install_overview_adjusted_budget_metric()


def install_finance_consistency() -> None:
    install_finance_consistency_ui()


__all__ = [
    "PATCH_MARKER",
    "boq_budget_total",
    "detail_boq_total",
    "saved_after_tax_total",
    "effective_proposed_value",
    "independent_vo_rows",
    "normalize_project_vo_values",
    "vo_summary",
    "install_finance_consistency_core",
    "install_finance_consistency_ui",
    "install_finance_consistency",
]
