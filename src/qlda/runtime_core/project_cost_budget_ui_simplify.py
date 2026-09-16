from __future__ import annotations

"""Simplify finance budget cards and align the executive overview with current commitments.

Budget/Baseline sheet:
- keep only BOQ hiện tại, Đường cơ sở chi phí and Tổng ngân sách dự án;
- keep reserve/baseline inputs inside the collapsed settings expander.

Executive overview:
- replace the legacy BOQ card with Ngân sách điều chỉnh;
- Ngân sách điều chỉnh uses the same ``committed_cost`` source as Kiểm soát chi phí;
- show the increase/decrease versus the current after-tax BOQ as the metric delta.
"""

from datetime import date, datetime
from functools import wraps
import inspect
from typing import Any

PATCH_MARKER = "V7.6 PROJECT COST BUDGET UI SIMPLIFY V2"


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _compact_money(value: Any, *, signed: bool = False) -> str:
    number = _number(value)
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


def _overview_context() -> tuple[Any, int] | None:
    """Return db/project only while the V7 executive overview is rendering."""
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


def _install_overview_adjusted_budget_metric(pcm) -> None:
    """Replace only the BOQ KPI inside ``render_overview_v7``.

    The overview calls ``c5.metric`` on a Streamlit DeltaGenerator, so patching
    ``st.metric`` alone would not affect it.  This wrapper is deliberately scoped
    by both the exact label and the render_overview_v7 call stack, leaving every
    other metric in the application unchanged.
    """
    try:
        from streamlit.delta_generator import DeltaGenerator
    except Exception:
        return

    if getattr(DeltaGenerator, "_qlda_overview_adjusted_budget_installed", False):
        return

    original_metric = DeltaGenerator.metric

    @wraps(original_metric)
    def metric_with_adjusted_budget(self, label, value, *args, **kwargs):
        if str(label or "").strip() == "BOQ":
            context = _overview_context()
            if context is not None:
                db, pid = context
                try:
                    snap = pcm.build_cost_snapshot(db, int(pid)) or {}
                    adjusted = max(0.0, _number(snap.get("committed_cost")))
                    boq = max(0.0, _number(snap.get("boq_estimate")))
                    label = "Ngân sách điều chỉnh"
                    value = _compact_money(adjusted)
                    # render_overview_v7 currently passes only label/value.  Add a
                    # comparison delta only when the caller has not supplied one.
                    if not args and "delta" not in kwargs:
                        kwargs["delta"] = f"{_compact_money(adjusted - boq, signed=True)} so BOQ"
                except Exception:
                    # Keep the legacy card if the finance snapshot cannot be read.
                    pass
        return original_metric(self, label, value, *args, **kwargs)

    DeltaGenerator.metric = metric_with_adjusted_budget
    DeltaGenerator._qlda_overview_adjusted_budget_installed = True
    DeltaGenerator._qlda_overview_adjusted_budget_marker = PATCH_MARKER


def install_project_cost_budget_ui_simplify() -> None:
    import qlda.runtime_core.project_cost_management as pcm

    # Install the executive overview patch independently so a hot-reloaded process
    # that already installed V1 can still receive the new overview behavior.
    _install_overview_adjusted_budget_metric(pcm)

    if getattr(pcm, "_qlda_project_cost_budget_ui_simplify_installed", False):
        return

    def render_budget_baseline_simplified(
        st,
        db,
        pid: int,
        *,
        identity: Any = None,
        can_update: bool = False,
    ) -> None:
        snap = pcm.build_cost_snapshot(db, int(pid))
        settings = snap["settings"]

        st.markdown("### 📊 Ngân sách & Đường cơ sở chi phí")
        st.caption(
            "Đường cơ sở chi phí gồm chi phí công việc trong baseline và dự phòng rủi ro; "
            "dự phòng quản lý nằm ngoài đường cơ sở chi phí."
        )

        # Only three non-duplicative KPI cards remain on the overview.
        c1, c2, c3 = st.columns(3)
        c1.metric("BOQ hiện tại", pcm._money(snap["boq_estimate"]))
        c2.metric("Đường cơ sở chi phí", pcm._money(snap["cost_baseline"]))
        c3.metric("Tổng ngân sách dự án", pcm._money(snap["total_budget"]))

        with st.expander("⚙️ Thiết lập đường cơ sở chi phí", expanded=False):
            st.caption(
                "Các thành phần chi tiết như chi phí công việc baseline, dự phòng rủi ro và "
                "dự phòng quản lý được quản lý tại đây thay vì lặp lại thành các ô KPI."
            )
            with st.form(f"project_cost_settings_{pid}"):
                c1, c2, c3 = st.columns(3)
                currency = c1.text_input(
                    "Tiền tệ",
                    value=pcm._text(settings.get("currency")) or "VND",
                )
                baseline_work = c2.number_input(
                    "Chi phí công việc trong baseline",
                    min_value=0.0,
                    value=float(snap["baseline_work_cost"]),
                    step=1_000_000.0,
                )
                baseline_date = c3.date_input(
                    "Ngày chốt baseline",
                    value=datetime.strptime(
                        (pcm._text(settings.get("baseline_date")) or date.today().isoformat())[:10],
                        "%Y-%m-%d",
                    ).date(),
                )

                c1, c2 = st.columns(2)
                contingency = c1.number_input(
                    "Dự phòng rủi ro",
                    min_value=0.0,
                    value=float(snap["contingency_reserve"]),
                    step=1_000_000.0,
                )
                management = c2.number_input(
                    "Dự phòng quản lý",
                    min_value=0.0,
                    value=float(snap["management_reserve"]),
                    step=1_000_000.0,
                )

                c1, c2 = st.columns(2)
                tolerance = c1.number_input(
                    "Dung sai ước tính (%)",
                    0.0,
                    100.0,
                    float(settings.get("estimate_tolerance_pct") or 5.0),
                    1.0,
                )
                threshold = c2.number_input(
                    "Ngưỡng kiểm soát chi phí (%)",
                    0.0,
                    100.0,
                    float(settings.get("control_threshold_pct") or 10.0),
                    1.0,
                )
                note = st.text_area("Ghi chú", value=pcm._text(settings.get("note")))

                if st.form_submit_button(
                    "💾 Lưu đường cơ sở chi phí",
                    disabled=not can_update,
                    use_container_width=True,
                ):
                    pcm.save_settings(
                        db,
                        pid,
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

    pcm.render_budget_baseline = render_budget_baseline_simplified
    pcm._qlda_project_cost_budget_ui_simplify_installed = True
    pcm._qlda_project_cost_budget_ui_simplify_marker = PATCH_MARKER


__all__ = ["install_project_cost_budget_ui_simplify"]
