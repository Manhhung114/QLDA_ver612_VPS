from __future__ import annotations

"""Presentation integration for PMBOK project cost management."""

from functools import wraps
import inspect

from qlda.runtime_core.project_cost_management import (
    PATCH_MARKER,
    _int,
    _register_postgres_tables,
    render_budget_baseline,
    render_cost_control,
)


def install_project_cost_management() -> None:
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
                        st,
                        db,
                        pid,
                        identity=identity,
                        can_update=can_update,
                        is_admin=bool(is_admin_fn()) if callable(is_admin_fn) else False,
                    )
                with all_tabs[5]:
                    render_cost_control(st, db, pid, identity=identity, can_update=can_update)

            return (all_tabs[0], all_tabs[2], all_tabs[3])
        finally:
            del caller
            del frame

    st.tabs = tabs_with_cost_management
    st._qlda_project_cost_management_installed = True
    st._qlda_project_cost_management_marker = PATCH_MARKER


__all__ = ["install_project_cost_management"]
