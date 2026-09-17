from __future__ import annotations

"""Uniform interaction policy for every sheet under Quản lý hồ sơ / Bản vẽ.

The meeting-minutes sheet established the simplest/clearest interaction model:
1. choose an existing record from the selector at the top;
2. the populated edit/view area opens automatically;
3. the attachment area is rendered immediately for the selected record and exposes
   the normal ``👁 Xem`` / ``⬇️ Tải`` actions;
4. a single checked row in the list opens that same record, while multi-selection
   remains available for bulk actions;
5. every selector shows enough identity to recognise a record without opening it.

The selector identity policy is intentionally uniform:
- documents: ``#ID - CODE - SUBJECT``;
- drawings: ``#ID - DRAWING_NO Rev.REVISION - TITLE``;
- meeting minutes: ``#ID - CODE - SUBJECT``.

This module applies the same selection semantics to NCR/RFA/RFI/BBHT/NTCV/NTVL/
KDVT/BBHOP and all drawing sheets without removing their own status, deadline or
approval-workflow logic.
"""

from functools import wraps
import inspect
from typing import Any

PATCH_MARKER = "V7.6 DOCUMENT MANAGEMENT UNIFORM INTERACTION V2"

_DOCUMENT_RENDERERS = {
    "render_document_type",
    "_render_approval_document_type",
    "render_meeting_minutes_simple",
}
_DRAWING_RENDERERS = {
    "render_drawing_type",
    "_render_approval_shopdrawing_type",
}
_RECORD_RENDERERS = _DOCUMENT_RENDERERS | _DRAWING_RENDERERS


def _record_render_context() -> dict[str, Any] | None:
    """Return locals for the active document/drawing renderer, if any."""
    frame = inspect.currentframe()
    try:
        current = frame.f_back if frame else None
        for _ in range(32):
            if current is None:
                break
            if current.f_code.co_name in _RECORD_RENDERERS:
                context = dict(current.f_locals)
                context["_qlda_renderer_name"] = current.f_code.co_name
                return context
            current = current.f_back
    finally:
        del frame
        try:
            del current
        except Exception:
            pass
    return None


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    if row is None:
        return default
    try:
        value = row[key]
    except Exception:
        try:
            value = dict(row).get(key, default)
        except Exception:
            return default
    return default if value is None else value


def _record_by_id(rows: Any, value: Any) -> Any | None:
    try:
        wanted = int(value)
    except Exception:
        return None
    try:
        for row in list(rows or []):
            try:
                if int(_row_value(row, "id", 0)) == wanted:
                    return row
            except Exception:
                continue
    except Exception:
        return None
    return None


def _clean_label_part(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _document_option_label(value: Any, rows: Any, fallback=None) -> str:
    if value is None:
        return "➕ Thêm mới"
    row = _record_by_id(rows, value)
    if row is None:
        if callable(fallback):
            try:
                return str(fallback(value))
            except Exception:
                pass
        return f"#{value}"

    code = _clean_label_part(_row_value(row, "code", ""))
    subject = _clean_label_part(_row_value(row, "subject", ""))
    parts = [f"#{int(value)}"]
    if code:
        parts.append(code)
    if subject:
        parts.append(subject)
    return " - ".join(parts)


def _drawing_option_label(value: Any, rows: Any, fallback=None) -> str:
    if value is None:
        return "➕ Thêm mới"
    row = _record_by_id(rows, value)
    if row is None:
        if callable(fallback):
            try:
                return str(fallback(value))
            except Exception:
                pass
        return f"#{value}"

    number = _clean_label_part(_row_value(row, "drawing_no", ""))
    revision = _clean_label_part(_row_value(row, "revision", ""))
    title = _clean_label_part(_row_value(row, "title", ""))

    identity = number
    if revision and revision not in {"-", "—"}:
        rev_text = revision if revision.lower().startswith("rev") else f"Rev.{revision}"
        identity = f"{identity} {rev_text}".strip()

    parts = [f"#{int(value)}"]
    if identity:
        parts.append(identity)
    if title:
        parts.append(title)
    return " - ".join(parts)


def install_document_management_uniform_interaction() -> None:
    import streamlit as st

    if getattr(st, "_qlda_document_management_uniform_interaction_installed", False):
        return

    original_selectbox = st.selectbox
    original_button = st.button

    @wraps(original_selectbox)
    def selectbox_uniform(label, options, *args, **kwargs):
        context = _record_render_context()
        label_text = str(label or "").strip()
        key = str(kwargs.get("key") or "")

        is_doc_selector = bool(
            context is not None
            and (
                key.startswith("doc_select_")
                or key.startswith("meeting_minutes_select_")
                or label_text.startswith("Chọn hồ sơ để")
                or label_text.startswith("Chọn biên bản để")
            )
        )
        is_drawing_selector = bool(
            context is not None
            and (
                key.startswith("drawing_select_")
                or (
                    str(context.get("_qlda_renderer_name") or "") in _DRAWING_RENDERERS
                    and label_text.startswith("Chọn ")
                    and " để " in label_text
                )
            )
        )

        if is_doc_selector or is_drawing_selector:
            # Keep approval-role intent visible where appropriate, otherwise use a
            # single wording across ordinary document sheets.
            if is_doc_selector and "phê duyệt" not in label_text and not label_text.startswith("Chọn biên bản"):
                label = "Chọn hồ sơ để xem / chỉnh sửa"

            rows = context.get("rows") or []
            original_format = kwargs.get("format_func")
            if is_drawing_selector:
                kwargs["format_func"] = lambda value: _drawing_option_label(value, rows, original_format)
            else:
                kwargs["format_func"] = lambda value: _document_option_label(value, rows, original_format)

            # The first option is None. Explicit index=0 prevents the browser from
            # showing the generic English "Choose an option" placeholder.
            if not args and "index" not in kwargs:
                kwargs["index"] = 0
            kwargs.setdefault("placeholder", "➕ Thêm mới / chọn hồ sơ để xem")

            # When an earlier grid action queued a record through *_pending, the
            # renderer moves it into the selectbox session-state key before this
            # call. Never overwrite that explicit selection with the default.
            if key and key in st.session_state:
                kwargs.pop("index", None)

        return original_selectbox(label, options, *args, **kwargs)

    @wraps(original_button)
    def button_uniform(label, *args, **kwargs):
        # A single checked row is auto-opened by document_selection_autopen, so the
        # old extra "Mở / xử lý hồ sơ" button is redundant. Hiding it makes every
        # document sheet behave like Biên bản họp while preserving Download/Delete.
        if _record_render_context() is not None and str(label or "").strip() == "📝 Mở / xử lý hồ sơ":
            return False
        return original_button(label, *args, **kwargs)

    st.selectbox = selectbox_uniform
    st.button = button_uniform
    st._qlda_document_management_uniform_interaction_installed = True
    st._qlda_document_management_uniform_interaction_marker = PATCH_MARKER


__all__ = [
    "install_document_management_uniform_interaction",
    "_document_option_label",
    "_drawing_option_label",
]
