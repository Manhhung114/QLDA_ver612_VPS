from __future__ import annotations

"""Use the persisted BOQ workbook after-tax total as the authoritative BAC.

The detailed BOQ rows in ``cost_budgets`` are work-item values before VAT.  A
saved multi-sheet BOQ workbook can also contain the contractual summary values
before tax, VAT and after tax.  For projects that have such a workbook, finance
screens use the after-tax amount as the BOQ/BAC basis.

V2 also repairs legacy cost-baseline settings that were auto-created before the
after-tax policy existed.  Those rows stored the detail BOQ sum (pre-tax) in
``baseline_work_cost`` and therefore continued to show the old 472.xx value
even after ``BOQ hiện tại`` correctly changed to 510.xx.  We migrate only a
baseline that still matches the detail-row total; a deliberately changed
baseline remains untouched.
"""

from datetime import datetime
from functools import wraps
import inspect
import math
from typing import Any

PATCH_MARKER = "V7.6 BOQ AFTER TAX BAC V2"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _close_money(a: Any, b: Any) -> bool:
    """Money-safe comparison for legacy values originating from Excel/SQL sums."""
    left = _float(a)
    right = _float(b)
    tolerance = max(1.0, abs(right) * 1e-9)
    return abs(left - right) <= tolerance


def saved_after_tax_total(db, project_id: int) -> float | None:
    """Return the persisted workbook after-tax total when available."""
    try:
        from qlda.runtime_core.boq_persistence import load_saved_boq_workbook

        result = load_saved_boq_workbook(db, int(project_id))
    except Exception:
        result = None
    if not isinstance(result, dict):
        return None

    # Parser persists after_tax_total and mirrors it to grand_total.
    for key in ("after_tax_total", "grand_total"):
        raw = result.get(key)
        if raw in (None, ""):
            continue
        value = _float(raw, -1.0)
        if value >= 0:
            return value
    return None


def detail_boq_total(db, project_id: int) -> float:
    """Historical detail-row total; normally the BOQ value before VAT."""
    pid = int(project_id)
    try:
        with db.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(budget_total),0) AS total FROM cost_budgets WHERE project_id=?",
                (pid,),
            ).fetchone()
            if row is not None:
                try:
                    return max(0.0, _float(row["total"]))
                except Exception:
                    return max(0.0, _float(row[0]))
    except Exception:
        pass
    try:
        return sum(max(0.0, _float(r["budget_total"])) for r in db.cost_budgets(pid))
    except Exception:
        return 0.0


def boq_budget_total(db, project_id: int) -> float:
    """Authoritative BOQ budget: after-tax workbook total, then detail fallback."""
    after_tax = saved_after_tax_total(db, int(project_id))
    if after_tax is not None:
        return max(0.0, float(after_tax))
    return detail_boq_total(db, int(project_id))


def _migrate_legacy_baseline(db, project_id: int, settings: dict[str, Any]) -> dict[str, Any]:
    """Convert an old auto-derived pre-tax baseline to the after-tax BAC basis.

    Safety rule: migration occurs only when the stored baseline still equals the
    current detail BOQ total.  A baseline that differs from that value is treated
    as an intentional user baseline and is preserved.
    """
    pid = int(project_id)
    after_tax = saved_after_tax_total(db, pid)
    if after_tax is None or after_tax <= 0:
        return settings

    detail_total = detail_boq_total(db, pid)
    baseline = _float(settings.get("baseline_work_cost"))
    if detail_total <= 0 or _close_money(after_tax, detail_total):
        return settings
    if not _close_money(baseline, detail_total):
        return settings

    try:
        with db.connect() as connection:
            connection.execute(
                "UPDATE project_cost_settings SET baseline_work_cost=?,updated_at=? "
                "WHERE workspace_project_id=?",
                (float(after_tax), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pid),
            )
    except Exception:
        # Even if persistence is temporarily unavailable, use the corrected value
        # for this render so BAC/Cost Baseline is not shown on a mixed tax basis.
        pass

    migrated = dict(settings)
    migrated["baseline_work_cost"] = float(after_tax)
    migrated["_qlda_after_tax_baseline_migrated"] = True
    return migrated


def _find_db_pid_from_stack() -> tuple[Any | None, int | None]:
    """Find the current finance renderer's db/project id without changing app.py."""
    frame = inspect.currentframe()
    current = frame.f_back if frame else None
    try:
        for _ in range(14):
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


def _patch_project_cost_management() -> None:
    """Keep Cost Baseline, EVM and AI on the same after-tax budget basis."""
    try:
        import qlda.runtime_core.project_cost_management as pcm
    except Exception:
        return

    if getattr(pcm, "_qlda_boq_after_tax_budget_v2_installed", False):
        return

    original_get_settings = pcm.get_settings
    original_evm_progress = pcm._evm_progress

    # build_cost_snapshot resolves this module global at runtime.
    pcm._boq_total = boq_budget_total

    @wraps(original_get_settings)
    def get_settings_after_tax(db, pid: int):
        settings = original_get_settings(db, int(pid))
        try:
            return _migrate_legacy_baseline(db, int(pid), dict(settings or {}))
        except Exception:
            return settings

    @wraps(original_evm_progress)
    def evm_progress_after_tax(db, pid: int, on_date):
        progress = dict(original_evm_progress(db, int(pid), on_date) or {})
        after_tax = saved_after_tax_total(db, int(pid))
        detail_total = detail_boq_total(db, int(pid))
        if after_tax is None or after_tax <= 0 or detail_total <= 0:
            return progress
        ratio = float(after_tax) / float(detail_total)
        if not math.isfinite(ratio) or ratio <= 0 or abs(ratio - 1.0) < 1e-12:
            return progress
        for key in ("pv", "ev", "linked_boq", "unlinked_boq"):
            progress[key] = max(0.0, _float(progress.get(key))) * ratio
        progress["tax_basis_ratio"] = ratio
        return progress

    pcm.get_settings = get_settings_after_tax
    pcm._evm_progress = evm_progress_after_tax
    pcm._qlda_boq_after_tax_budget_v2_installed = True
    pcm._qlda_boq_after_tax_budget_marker = PATCH_MARKER


def _patch_top_bac_metric() -> None:
    """Fix the legacy BOQ-tab BAC metric without rewriting the large app entrypoint."""
    import streamlit as st

    if getattr(st, "_qlda_boq_after_tax_bac_metric_installed", False):
        return
    original_metric = st.metric

    @wraps(original_metric)
    def metric_after_tax(label, value, *args, **kwargs):
        label_text = str(label or "").strip()
        if label_text == "Tổng ngân sách kế hoạch (BAC)":
            db, pid = _find_db_pid_from_stack()
            if db is not None and pid is not None:
                after_tax = saved_after_tax_total(db, pid)
                if after_tax is not None:
                    label = "Tổng ngân sách kế hoạch sau thuế (BAC)"
                    value = f"{after_tax:,.0f} VND"
        return original_metric(label, value, *args, **kwargs)

    st.metric = metric_after_tax
    st._qlda_boq_after_tax_bac_metric_installed = True
    st._qlda_boq_after_tax_bac_metric_marker = PATCH_MARKER


def install_boq_after_tax_budget_policy() -> None:
    _patch_project_cost_management()
    _patch_top_bac_metric()


__all__ = [
    "boq_budget_total",
    "detail_boq_total",
    "saved_after_tax_total",
    "install_boq_after_tax_budget_policy",
]
