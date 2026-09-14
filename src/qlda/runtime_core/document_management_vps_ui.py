from __future__ import annotations

"""Document-management UI adaptations for the VPS deployment.

All document sheets keep the existing upload/download implementation, but the
visible file area is presented as VPS storage rather than Google Drive. Meeting
minutes remain a simple archive and deliberately expose no dedicated AI action;
AI access is centralized under Công cụ -> Trợ lý AI.
"""

import inspect
from typing import Any

from qlda.runtime_core import meeting_minutes_simple as meeting


_PATCH_FLAG = "_qlda_document_vps_attachment_renderer"


def _find_app_globals() -> dict[str, Any] | None:
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame else None
        while frame is not None:
            glob = frame.f_globals
            cfg = glob.get("DOC_CONFIG")
            if isinstance(cfg, dict) and "NCR" in cfg and "BBHT" in cfg:
                return glob
            frame = frame.f_back
    finally:
        del frame
    return None


def _install_meeting_minutes_without_ai(st) -> None:
    if getattr(meeting, "_qlda_meeting_minutes_no_ai_installed", False):
        return

    original_render = meeting.render_meeting_minutes_simple

    def _render_without_ai(st_obj, app_globals: dict[str, Any], pid: int):
        # Clear stale output created by older deployments.
        prefix = f"meeting_minutes_ai_review_{int(pid)}_"
        for key in list(st_obj.session_state.keys()):
            if str(key).startswith(prefix):
                st_obj.session_state.pop(key, None)

        original_button = st_obj.button

        def _button(label, *args, **kwargs):
            text = str(label or "")
            if "Rà soát" in text and "AI" in text:
                # Do not instantiate a button at all. The only AI entry point is
                # now Công cụ -> Trợ lý AI.
                return False
            return original_button(label, *args, **kwargs)

        st_obj.button = _button
        try:
            return original_render(st_obj, app_globals, int(pid))
        finally:
            st_obj.button = original_button

    def _disabled_private_ai(*_args, **_kwargs):
        raise RuntimeError("Biên bản họp không có AI riêng; hãy dùng Công cụ -> Trợ lý AI.")

    meeting.render_meeting_minutes_simple = _render_without_ai
    meeting._build_ai = _disabled_private_ai
    meeting._qlda_meeting_minutes_no_ai_installed = True


def _patch_attachment_renderer(st, app_globals: dict[str, Any]) -> None:
    if app_globals.get(_PATCH_FLAG):
        return

    original = app_globals.get("_render_inline_drive_attachments")
    if not callable(original):
        return

    def _render_vps_attachments(*args, **kwargs):
        kind = str(kwargs.get("kind") or "")
        if kind != "document":
            return original(*args, **kwargs)

        # The gateway already routes local mode to Local VPS Storage. Keep the
        # proven file actions but remove stale Google-Drive-specific UI elements.
        original_markdown = st.markdown
        original_link_button = st.link_button
        original_checkbox = st.checkbox
        original_info = st.info
        original_error = st.error
        original_warning = st.warning

        def _markdown(body, *m_args, **m_kwargs):
            text = str(body or "")
            if text.strip() == "**Danh sách file Google Drive**":
                return original_markdown("**File đính kèm trên VPS**", *m_args, **m_kwargs)
            return original_markdown(body, *m_args, **m_kwargs)

        def _link_button(label, *l_args, **l_kwargs):
            text = str(label or "")
            # In local VPS mode there is no separate cloud-drive destination to
            # expose. Xem/Tải remain available through signed local URLs.
            if "Google Drive" in text or text.strip() in {"☁ Drive", "Drive"}:
                return None
            return original_link_button(label, *l_args, **l_kwargs)

        def _checkbox(label, *c_args, **c_kwargs):
            if str(label or "").strip() == "Hiện cả _Lich_su":
                return False
            return original_checkbox(label, *c_args, **c_kwargs)

        def _replace_storage_word(value):
            if isinstance(value, str):
                return value.replace("Google Drive", "VPS").replace("trên Drive", "trên VPS")
            return value

        def _info(body, *i_args, **i_kwargs):
            return original_info(_replace_storage_word(body), *i_args, **i_kwargs)

        def _error(body, *e_args, **e_kwargs):
            return original_error(_replace_storage_word(body), *e_args, **e_kwargs)

        def _warning(body, *w_args, **w_kwargs):
            return original_warning(_replace_storage_word(body), *w_args, **w_kwargs)

        st.markdown = _markdown
        st.link_button = _link_button
        st.checkbox = _checkbox
        st.info = _info
        st.error = _error
        st.warning = _warning
        try:
            return original(*args, **kwargs)
        finally:
            st.markdown = original_markdown
            st.link_button = original_link_button
            st.checkbox = original_checkbox
            st.info = original_info
            st.error = original_error
            st.warning = original_warning

    app_globals["_render_inline_drive_attachments"] = _render_vps_attachments
    app_globals[_PATCH_FLAG] = True


def install_document_management_vps_ui() -> None:
    """Apply VPS file presentation to every sheet in Quản lý hồ sơ."""
    import streamlit as st

    if getattr(st, "_qlda_document_management_vps_ui_installed", False):
        return

    # Preserve the status-free meeting-minutes renderer introduced earlier, then
    # remove its dedicated AI entry point.
    meeting.install_meeting_minutes_simple_ui()
    _install_meeting_minutes_without_ai(st)

    original_segmented = st.segmented_control

    def _segmented_control(label, options, *args, **kwargs):
        result = original_segmented(label, options, *args, **kwargs)
        if str(label or "").strip() == "Loại hồ sơ":
            app_globals = _find_app_globals()
            if app_globals is not None:
                _patch_attachment_renderer(st, app_globals)
        return result

    st._qlda_document_management_vps_original_segmented = original_segmented
    st.segmented_control = _segmented_control
    st._qlda_document_management_vps_ui_installed = True
