from __future__ import annotations

"""Simplify the budget/baseline KPI area in Project Cost Management.

Keep only the three decision-level figures at the top of the sheet:
- BOQ hiện tại
- Đường cơ sở chi phí
- Tổng ngân sách dự án

Detailed components (baseline work cost, contingency reserve, management reserve,
tolerance and threshold) remain editable inside the existing collapsed settings
expander, so no financial capability is removed.
"""

from datetime import date, datetime
from typing import Any

PATCH_MARKER = "V7.6 PROJECT COST BUDGET UI SIMPLIFY V1"


def install_project_cost_budget_ui_simplify() -> None:
    import qlda.runtime_core.project_cost_management as pcm

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
