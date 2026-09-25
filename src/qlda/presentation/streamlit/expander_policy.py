from __future__ import annotations

"""Presentation-owned global Streamlit expander policy for QLDA."""

from functools import wraps
import inspect

PATCH_MARKER = "V7.6 UI EXPANDERS DEFAULT COLLAPSED V3 PRESENTATION"

_EDIT_EXPANDER_LABELS = {
    "✏️ Thêm / sửa hồ sơ",
    "✏️ Thêm / sửa bản vẽ",
    "📝 Thêm / sửa biên bản họp",
}
_EDIT_RENDERERS = {
    "render_document_type",
    "_render_approval_document_type",
    "render_drawing_type",
    "_render_approval_shopdrawing_type",
    "render_meeting_minutes_simple",
}


def _editing_existing_record() -> bool:
    frame = inspect.currentframe()
    try:
        current = frame.f_back if frame else None
        for _ in range(24):
            if current is None:
                break
            if current.f_code.co_name in _EDIT_RENDERERS:
                selected = current.f_locals.get("selected")
                if isinstance(selected, bool):
                    return False
                if isinstance(selected, int):
                    return selected > 0
                if isinstance(selected, str):
                    return bool(selected.strip())
                return selected is not None
            current = current.f_back
    finally:
        del frame
        try:
            del current
        except Exception:
            pass
    return False


def install_expanders_default_collapsed() -> None:
    import streamlit as st

    if getattr(st, "_qlda_expanders_default_collapsed_installed", False):
        return

    original_expander = st.expander

    @wraps(original_expander)
    def expander_collapsed(label, *args, **kwargs):
        label_text = str(label or "").strip()
        auto_expand = label_text in _EDIT_EXPANDER_LABELS and _editing_existing_record()
        args_list = list(args)
        if args_list and isinstance(args_list[0], bool):
            args_list[0] = bool(auto_expand)
        else:
            kwargs["expanded"] = bool(auto_expand)
        return original_expander(label, *args_list, **kwargs)

    st.expander = expander_collapsed
    st._qlda_expanders_default_collapsed_installed = True
    st._qlda_expanders_default_collapsed_marker = PATCH_MARKER


__all__ = ["install_expanders_default_collapsed"]
