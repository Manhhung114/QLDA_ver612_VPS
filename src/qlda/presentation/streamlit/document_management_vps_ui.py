from __future__ import annotations

"""Document-management UI adaptations for the VPS deployment.

All document/drawing sheets keep the existing upload/download implementation, but
the visible file area is presented as VPS storage rather than Google Drive.
Meeting minutes remain a simple status-free archive. AI is available only from
Công cụ -> Trợ lý AI.

V7.6 restores the expected attachment interaction for an existing selected record:
pressing ``📎 Đính kèm file`` or ``🔄 Làm mới file / File DB`` creates a fresh
direct-upload ticket *before* Streamlit performs the widget-triggered rerun. This
matters because ``st.rerun()`` raises immediately; post-click wrapper code is never
reached by the legacy refresh handler. The pre-click callback is applied to
NCR/RFA/RFI/BBHT/NTCV/NTVL/KDVT/BBHOP and every drawing sheet using the common
attachment renderer.
"""

import inspect
from typing import Any

from qlda.runtime_core import meeting_minutes_simple as meeting


_PATCH_FLAG = "_qlda_document_vps_attachment_renderer"
PATCH_MARKER = "V7.6 VPS ATTACHMENT REOPEN V3"


def _find_app_globals() -> dict[str, Any] | None:
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame else None
        while frame is not None:
            glob = frame.f_globals
            cfg = glob.get("DOC_CONFIG")
            if isinstance(cfg, dict) and "NCR" in cfg and "BBHT" in cfg:
                return glob
            app_globals = frame.f_locals.get("app_globals")
            if isinstance(app_globals, dict) and callable(app_globals.get("_prepare_inline_upload_ticket")):
                return app_globals
            frame = frame.f_back
    finally:
        del frame
    return None


def _row_value(row: Any, key: str) -> str:
    if row is None:
        return ""
    try:
        value = row[key]
    except Exception:
        try:
            value = dict(row).get(key, "")
        except Exception:
            return ""
    return str(value or "").strip()


def _active_upload_context() -> dict[str, Any] | None:
    """Resolve the selected file owner from the active Streamlit render stack."""
    frame = inspect.currentframe()
    try:
        current = frame.f_back if frame else None
        for _ in range(40):
            if current is None:
                break
            loc = current.f_locals
            name = current.f_code.co_name

            if name in {"_render_inline_drive_attachments", "_render_vps_attachments"}:
                pid = loc.get("pid")
                if pid is None:
                    call_args = loc.get("args") or ()
                    if call_args:
                        pid = call_args[0]
                call_kwargs = loc.get("kwargs") if isinstance(loc.get("kwargs"), dict) else {}
                kind = str(loc.get("kind") or call_kwargs.get("kind") or "").strip()
                subtype = str(loc.get("subtype") or call_kwargs.get("subtype") or "").strip()
                record_code = str(loc.get("record_code") or call_kwargs.get("record_code") or "").strip()
                panel_key = str(loc.get("panel_key") or call_kwargs.get("panel_key") or "").strip()
                app_globals = _find_app_globals()
                if pid is not None and kind and subtype and record_code and panel_key and app_globals:
                    return {
                        "pid": int(pid),
                        "kind": kind,
                        "subtype": subtype,
                        "record_code": record_code,
                        "panel_key": panel_key,
                        "app_globals": app_globals,
                    }

            renderer_kind = ""
            subtype_name = ""
            code_field = ""
            panel_prefix = ""
            if name in {"render_document_type", "_render_approval_document_type"}:
                renderer_kind, subtype_name, code_field, panel_prefix = "document", "doc_type", "code", "v6_doc_attach"
            elif name in {"render_drawing_type", "_render_approval_shopdrawing_type"}:
                renderer_kind, subtype_name, code_field, panel_prefix = "drawing", "drawing_type", "drawing_no", "v6_drawing_attach"
            elif name == "render_meeting_minutes_simple":
                renderer_kind, subtype_name, code_field, panel_prefix = "document", "", "code", "meeting_minutes_files"

            if renderer_kind:
                selected = loc.get("selected")
                pid = loc.get("pid")
                if selected not in (None, "") and pid is not None:
                    try:
                        rid = int(selected)
                    except Exception:
                        rid = 0
                    db = loc.get("db")
                    app_globals = loc.get("app_globals") if isinstance(loc.get("app_globals"), dict) else None
                    if app_globals is None:
                        app_globals = _find_app_globals()
                    if rid and db is not None and app_globals:
                        try:
                            row = db.drawing(rid) if renderer_kind == "drawing" else db.document(rid)
                        except Exception:
                            row = None
                        code = _row_value(row, code_field)
                        subtype = "BBHOP" if name == "render_meeting_minutes_simple" else str(loc.get(subtype_name) or "").strip()
                        if code and subtype:
                            return {
                                "pid": int(pid),
                                "kind": renderer_kind,
                                "subtype": subtype,
                                "record_code": code,
                                "panel_key": f"{panel_prefix}_{int(pid)}_{subtype}_{rid}" if panel_prefix.startswith("v6_") else f"{panel_prefix}_{int(pid)}_{rid}",
                                "app_globals": app_globals,
                            }
            current = current.f_back
    finally:
        del frame
        try:
            del current
        except Exception:
            pass
    return None


def _prepare_upload_context(st, context: dict[str, Any] | None) -> bool:
    """Create a fresh direct-upload ticket from an already resolved context."""
    if not context:
        return False
    app_globals = context.get("app_globals") or {}
    prepare = app_globals.get("_prepare_inline_upload_ticket")
    if not callable(prepare):
        return False

    panel_key = str(context.get("panel_key") or "").strip()
    if not panel_key:
        return False
    st.session_state.pop(panel_key + "_ticket", None)
    st.session_state.pop(panel_key + "_upload_open", None)
    st.session_state.pop(panel_key + "_ticket_error", None)
    try:
        prepare(
            int(context["pid"]),
            kind=str(context["kind"]),
            subtype=str(context["subtype"]),
            record_code=str(context["record_code"]),
            panel_key=panel_key,
        )
        return True
    except Exception as exc:
        st.session_state[panel_key + "_ticket_error"] = f"Chưa mở được vùng tải file lên VPS: {exc}"
        return False


def _prepare_selected_upload(st) -> bool:
    """Create a new upload ticket for the selected existing record."""
    return _prepare_upload_context(st, _active_upload_context())


def _install_preclick_upload_callback(st, widget_kwargs: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """Run ticket preparation in the widget callback, before any legacy rerun."""
    if not context:
        return widget_kwargs

    out = dict(widget_kwargs)
    existing_callback = out.pop("on_click", None)
    existing_args = out.pop("args", None) or ()
    existing_kwargs = out.pop("kwargs", None) or {}

    def _before_rerun_callback():
        _prepare_upload_context(st, context)
        if callable(existing_callback):
            existing_callback(*existing_args, **existing_kwargs)

    out["on_click"] = _before_rerun_callback
    return out


def _patch_attachment_renderer(st, app_globals: dict[str, Any]) -> None:
    if app_globals.get(_PATCH_FLAG):
        return

    original = app_globals.get("_render_inline_drive_attachments")
    if not callable(original):
        return

    def _render_vps_attachments(*args, **kwargs):
        kind = str(kwargs.get("kind") or "")
        if kind not in {"document", "drawing"}:
            return original(*args, **kwargs)

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
    """Apply VPS file presentation/upload behavior to every supported sheet."""
    import streamlit as st

    if getattr(st, "_qlda_document_management_vps_ui_installed", False):
        return

    meeting.install_meeting_minutes_simple_ui()

    original_segmented = st.segmented_control
    original_form_submit = st.form_submit_button
    original_button = st.button

    def _segmented_control(label, options, *args, **kwargs):
        result = original_segmented(label, options, *args, **kwargs)
        if str(label or "").strip() == "Loại hồ sơ":
            app_globals = _find_app_globals()
            if app_globals is not None:
                _patch_attachment_renderer(st, app_globals)
        return result

    def _form_submit_button(label, *args, **kwargs):
        text = str(label or "").strip()
        if "Đính kèm file" in text:
            context = _active_upload_context()
            kwargs = _install_preclick_upload_callback(st, kwargs, context)
        return original_form_submit(label, *args, **kwargs)

    def _button(label, *args, **kwargs):
        text = str(label or "").strip()
        if (
            "Làm mới file / File DB" in text
            or "Đính kèm file" in text
            or text == "📤 Tải file lên lưu"
        ):
            context = _active_upload_context()
            kwargs = _install_preclick_upload_callback(st, kwargs, context)
        return original_button(label, *args, **kwargs)

    st._qlda_document_management_vps_original_segmented = original_segmented
    st._qlda_document_management_vps_original_form_submit_button = original_form_submit
    st._qlda_document_management_vps_original_button = original_button
    st.segmented_control = _segmented_control
    st.form_submit_button = _form_submit_button
    st.button = _button
    st._qlda_document_management_vps_ui_installed = True
    st._qlda_document_management_vps_ui_marker = PATCH_MARKER


__all__ = ["PATCH_MARKER", "install_document_management_vps_ui"]
