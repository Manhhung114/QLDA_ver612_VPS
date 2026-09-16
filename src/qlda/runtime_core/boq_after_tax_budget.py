from __future__ import annotations

"""Use the persisted BOQ workbook after-tax total as the project BAC/BOQ value.

The detailed BOQ rows in ``cost_budgets`` are pre-tax work items. When a saved
multi-sheet BOQ workbook contains a summary sheet with VAT and an after-tax
total, the financial budget shown to users must use that after-tax amount.
Manual BOQ projects without a saved workbook keep the historical detail-sum
fallback.
"""

from functools import wraps
import inspect
import math
from typing import Any

PATCH_MARKER = "V7.6 BOQ AFTER TAX BAC V1"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def saved_after_tax_total(db, project_id: int) -> float | None:
    """Return the saved workbook after-tax total when it is available."""
    try:
        from qlda.runtime_core.boq_persistence import load_saved_boq_workbook

        result = load_saved_boq_workbook(db, int(project_id))
    except Exception:
        result = None
    if not isinstance(result, dict):
        return None

    # Parser persists after_tax_total and also mirrors it to grand_total.
    for key in ("after_tax_total", "grand_total"):
        raw = result.get(key)
        if raw in (None, ""):
            continue
        value = _float(raw, -1.0)
        if value >= 0:
            return value
    return None


def detail_boq_total(db, project_id: int) -> float:
    """Historical fallback for manual BOQ/no persisted workbook."""
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


def _find_db_pid_from_stack() -> tuple[Any | None, int | None]:
    """Find the current finance renderer's db/project id without changing app.py."""
    frame = inspect.currentframe()
    current = frame.f_back if frame else None
    try:
        for _ in range(12):
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
    """Make Cost Baseline/EVM/AI use the same after-tax BOQ budget."""
    try:
        import qlda.runtime_core.project_cost_management as pcm

        pcm._boq_total = boq_budget_total
        pcm._qlda_boq_after_tax_budget_marker = PATCH_MARKER
    except Exception:
        pass


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
