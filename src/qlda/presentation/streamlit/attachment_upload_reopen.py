from __future__ import annotations

"""Reliable reopen behavior for attachment uploaders on existing records.

The legacy Streamlit renderers keep ``db`` as a module global, not a function local.
Older callback bridges therefore failed to resolve the selected record context for
normal document/drawing forms and no upload ticket was created when the user pressed
``Đính kèm file``. This installer resolves both local and global renderer state and
prepares a fresh VPS upload ticket in the widget callback, before any legacy rerun.

It applies to all document sheets, meeting minutes and every drawing sheet.
"""

from functools import wraps
import inspect
from typing import Any

PATCH_MARKER = "V7.6 ATTACHMENT UPLOAD REOPEN FIX V4"


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


def _find_context() -> dict[str, Any] | None:
    frame = inspect.currentframe()
    try:
        current = frame.f_back if frame else None
        for _ in range(48):
            if current is None:
                break
            loc = current.f_locals
            glob = current.f_globals
            name = current.f_code.co_name

            if name in {"_render_inline_drive_attachments", "_render_vps_attachments"}:
                call_kwargs = loc.get("kwargs") if isinstance(loc.get("kwargs"), dict) else {}
                call_args = loc.get("args") or ()
                pid = loc.get("pid")
                if pid is None and call_args:
                    pid = call_args[0]
                kind = str(loc.get("kind") or call_kwargs.get("kind") or "").strip()
                subtype = str(loc.get("subtype") or call_kwargs.get("subtype") or "").strip()
                code = str(loc.get("record_code") or call_kwargs.get("record_code") or "").strip()
                panel_key = str(loc.get("panel_key") or call_kwargs.get("panel_key") or "").strip()
                prepare = glob.get("_prepare_inline_upload_ticket")
                if not callable(prepare):
                    app_globals = loc.get("app_globals")
                    if isinstance(app_globals, dict):
                        prepare = app_globals.get("_prepare_inline_upload_ticket")
                if pid is not None and kind and subtype and code and panel_key and callable(prepare):
                    return {
                        "pid": int(pid), "kind": kind, "subtype": subtype,
                        "record_code": code, "panel_key": panel_key, "prepare": prepare,
                    }

            kind = ""
            subtype_name = ""
            code_field = ""
            prefix = ""
            if name in {"render_document_type", "_render_approval_document_type"}:
                kind, subtype_name, code_field, prefix = "document", "doc_type", "code", "v6_doc_attach"
            elif name in {"render_drawing_type", "_render_approval_shopdrawing_type"}:
                kind, subtype_name, code_field, prefix = "drawing", "drawing_type", "drawing_no", "v6_drawing_attach"
            elif name == "render_meeting_minutes_simple":
                kind, subtype_name, code_field, prefix = "document", "", "code", "meeting_minutes_files"

            if kind:
                selected = loc.get("selected")
                pid = loc.get("pid")
                if selected not in (None, "") and pid is not None:
                    try:
                        rid = int(selected)
                    except Exception:
                        rid = 0
                    db = loc.get("db") or glob.get("db")
                    app_globals = loc.get("app_globals") if isinstance(loc.get("app_globals"), dict) else glob
                    prepare = app_globals.get("_prepare_inline_upload_ticket") if isinstance(app_globals, dict) else None
                    if rid and db is not None and callable(prepare):
                        try:
                            row = db.drawing(rid) if kind == "drawing" else db.document(rid)
                        except Exception:
                            row = None
                        code = _row_value(row, code_field)
                        subtype = "BBHOP" if name == "render_meeting_minutes_simple" else str(loc.get(subtype_name) or "").strip()
                        if code and subtype:
                            panel_key = (
                                f"{prefix}_{int(pid)}_{subtype}_{rid}"
                                if prefix.startswith("v6_")
                                else f"{prefix}_{int(pid)}_{rid}"
                            )
                            return {
                                "pid": int(pid), "kind": kind, "subtype": subtype,
                                "record_code": code, "panel_key": panel_key, "prepare": prepare,
                            }
            current = current.f_back
    finally:
        del frame
        try:
            del current
        except Exception:
            pass
    return None


def _prepare(st, context: dict[str, Any] | None) -> None:
    if not context:
        return
    panel_key = str(context.get("panel_key") or "")
    prepare = context.get("prepare")
    if not panel_key or not callable(prepare):
        return
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
        st.session_state[panel_key + "_force_upload_visible"] = True
    except Exception as exc:
        st.session_state[panel_key + "_ticket_error"] = f"Chưa mở được vùng tải file lên VPS: {exc}"


def _with_callback(st, kwargs: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return kwargs
    out = dict(kwargs)
    previous = out.pop("on_click", None)
    previous_args = out.pop("args", None) or ()
    previous_kwargs = out.pop("kwargs", None) or {}

    def callback():
        _prepare(st, context)
        if callable(previous):
            previous(*previous_args, **previous_kwargs)

    out["on_click"] = callback
    return out


def install_attachment_upload_reopen_fix() -> None:
    import streamlit as st

    if getattr(st, "_qlda_attachment_upload_reopen_fix_installed", False):
        return

    original_form_submit = st.form_submit_button
    original_button = st.button

    @wraps(original_form_submit)
    def form_submit(label, *args, **kwargs):
        if "Đính kèm file" in str(label or ""):
            kwargs = _with_callback(st, kwargs, _find_context())
        return original_form_submit(label, *args, **kwargs)

    @wraps(original_button)
    def button(label, *args, **kwargs):
        text = str(label or "").strip()
        if (
            "Làm mới file / File DB" in text
            or "Đính kèm file" in text
            or text == "📤 Tải file lên lưu"
            or "Tạo lại link tải file" in text
        ):
            kwargs = _with_callback(st, kwargs, _find_context())
        return original_button(label, *args, **kwargs)

    st.form_submit_button = form_submit
    st.button = button
    st._qlda_attachment_upload_reopen_fix_installed = True
    st._qlda_attachment_upload_reopen_fix_marker = PATCH_MARKER


__all__ = ["PATCH_MARKER", "install_attachment_upload_reopen_fix"]
