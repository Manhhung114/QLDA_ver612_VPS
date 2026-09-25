from __future__ import annotations

from typing import Any, Iterable

AI_NAV_LABEL = "🤖 AI Supervisor"
HOME_NAV_LABEL = "🏠 Tổng quan"
NAV_RADIO_LABEL = "Nhóm chức năng"
_NAV_ACTIVE_KEY = "qlda_ai_supervisor_navigation_active"
PATCH_MARKER = "AI SUPERVISOR DEDICATED NAVIGATION V1"


def _inject_ai_nav_option(options: Iterable[Any]) -> list[Any]:
    values = list(options)
    if AI_NAV_LABEL in values:
        return values
    try:
        index = values.index(HOME_NAV_LABEL) + 1
    except ValueError:
        index = len(values)
    values.insert(index, AI_NAV_LABEL)
    return values


def _is_main_navigation(label: Any, options: Iterable[Any]) -> bool:
    if str(label or "").strip() != NAV_RADIO_LABEL:
        return False
    values = {str(value) for value in list(options)}
    return HOME_NAV_LABEL in values and "📚 Công cụ" in values


def install_ai_supervisor_navigation() -> None:
    """Expose AI Supervisor as its own sidebar navigation item.

    The production app keeps the V7 navigation dispatch in ``app.py``. Rather than
    duplicating that large entry point, this targeted runtime extension augments the
    single business-group radio. When the dedicated AI item is selected, the app is
    routed through the existing Overview dispatch but the Overview renderer is
    replaced for that rerun with the contractor-isolated AI Supervisor page only.

    The existing Supervisor/advanced UI remains unchanged and therefore preserves
    tenant isolation, RBAC, approvals and audit behavior.
    """
    import streamlit as st
    import qlda.runtime_core.autonomy_overview_patch as overview
    import qlda.runtime_core.ui_v7_compact as ui

    if getattr(st, "_qlda_ai_supervisor_navigation_installed", False):
        return

    original_radio = st.radio
    original_overview_renderer = ui.render_overview_v7
    final_supervisor_renderer = overview.render_autonomy_control_center

    def radio_with_ai_supervisor(label, options, *args, **kwargs):
        try:
            raw_options = list(options)
        except Exception:
            raw_options = options

        if isinstance(raw_options, list) and _is_main_navigation(label, raw_options):
            nav_options = _inject_ai_nav_option(raw_options)
            selected = original_radio(label, nav_options, *args, **kwargs)
            active = str(selected) == AI_NAV_LABEL
            st.session_state[_NAV_ACTIVE_KEY] = active
            # Keep the existing app dispatch stable: the wrapper around
            # render_overview_v7 below decides whether to show Overview or AI.
            return HOME_NAV_LABEL if active else selected

        return original_radio(label, raw_options, *args, **kwargs)

    def render_overview_or_supervisor(st_obj, db, project_id: int, *args, **kwargs):
        if bool(st_obj.session_state.get(_NAV_ACTIVE_KEY, False)):
            return final_supervisor_renderer(
                st_obj,
                db,
                int(project_id),
                ui_module=ui,
            )
        return original_overview_renderer(st_obj, db, int(project_id), *args, **kwargs)

    st._qlda_ai_supervisor_original_radio = original_radio
    st.radio = radio_with_ai_supervisor
    ui.render_overview_v7 = render_overview_or_supervisor
    st._qlda_ai_supervisor_navigation_installed = True
    st._qlda_ai_supervisor_navigation_marker = PATCH_MARKER


__all__ = [
    "AI_NAV_LABEL",
    "HOME_NAV_LABEL",
    "NAV_RADIO_LABEL",
    "PATCH_MARKER",
    "_inject_ai_nav_option",
    "_is_main_navigation",
    "install_ai_supervisor_navigation",
]
