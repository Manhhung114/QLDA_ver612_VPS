from __future__ import annotations

"""Uniform interaction policy for every sheet under Quản lý hồ sơ.

The meeting-minutes sheet established the simplest/clearest interaction model:
1. choose an existing record from the selector at the top;
2. the populated edit/view area opens automatically;
3. the attachment area is rendered immediately for the selected record and exposes
   the normal ``👁 Xem`` / ``⬇️ Tải`` actions;
4. a single checked row in the list opens that same record, while multi-selection
   remains available for bulk actions.

This module applies the same selection semantics to NCR/RFA/RFI/BBHT/NTCV/NTVL/
KDVT without removing their own status, deadline or approval-workflow logic.
"""

from functools import wraps
import inspect
from typing import Any

PATCH_MARKER = "V7.6 DOCUMENT MANAGEMENT UNIFORM INTERACTION V1"

_DOCUMENT_RENDERERS = {
    "render_document_type",
    "_render_approval_document_type",
    "render_meeting_minutes_simple",
}


def _document_render_context() -> dict[str, Any] | None:
    """Return locals for the active document renderer, if any."""
    frame = inspect.currentframe()
    try:
        current = frame.f_back if frame else None
        for _ in range(28):
            if current is None:
                break
            if current.f_code.co_name in _DOCUMENT_RENDERERS:
                return dict(current.f_locals)
            current = current.f_back
    finally:
        del frame
        try:
            del current
        except Exception:
            pass
    return None


def install_document_management_uniform_interaction() -> None:
    import streamlit as st

    if getattr(st, "_qlda_document_management_uniform_interaction_installed", False):
        return

    original_selectbox = st.selectbox
    original_button = st.button

    @wraps(original_selectbox)
    def selectbox_uniform(label, options, *args, **kwargs):
        context = _document_render_context()
        label_text = str(label or "").strip()

        # Normal document sheets used several slightly different labels and some
        # Streamlit versions rendered an English "Choose an option" placeholder.
        # Make the selector deterministic and use the meeting-minutes wording.
        if context is not None and label_text.startswith("Chọn hồ sơ để"):
            key = str(kwargs.get("key") or "")

            # Keep approval-role intent visible where appropriate, otherwise use a
            # single wording across all ordinary document sheets.
            if "phê duyệt" not in label_text:
                label = "Chọn hồ sơ để xem / chỉnh sửa"

            # The first option in all document selectors is None and its existing
            # format_func renders "➕ Thêm mới".  Explicit index=0 prevents the
            # browser from showing the generic English placeholder.
            if not args and "index" not in kwargs:
                kwargs["index"] = 0
            kwargs.setdefault("placeholder", "➕ Thêm mới / chọn hồ sơ để xem")

            # When an earlier grid action queued a record through *_pending, the
            # renderer moves it into the selectbox session-state key before this
            # call.  Never overwrite that explicit selection with the default.
            if key and key in st.session_state:
                kwargs.pop("index", None)

        return original_selectbox(label, options, *args, **kwargs)

    @wraps(original_button)
    def button_uniform(label, *args, **kwargs):
        # A single checked row is auto-opened by document_selection_autopen, so the
        # old extra "Mở / xử lý hồ sơ" button is redundant.  Hiding it makes every
        # document sheet behave like Biên bản họp while preserving Download/Delete.
        if _document_render_context() is not None and str(label or "").strip() == "📝 Mở / xử lý hồ sơ":
            return False
        return original_button(label, *args, **kwargs)

    st.selectbox = selectbox_uniform
    st.button = button_uniform
    st._qlda_document_management_uniform_interaction_installed = True
    st._qlda_document_management_uniform_interaction_marker = PATCH_MARKER


__all__ = ["install_document_management_uniform_interaction"]
