from __future__ import annotations

"""Auto-open a document/drawing selected from the list grid.

The legacy screen has two independent selection mechanisms:
1) the upper selectbox used to load/edit a record;
2) the checkbox column in the list grid used for bulk actions.

Users naturally tick a row expecting to view it. This bridge turns a single row
selection into the same pending selection used by the upper selectbox, then reruns
once. On the next render the edit form is populated and the attachment renderer
shows the existing file list with the normal ``👁 Xem`` / ``⬇️ Tải`` controls.

Multi-selection is deliberately left as a bulk-selection operation and does not
auto-open any record.
"""

from functools import wraps
import inspect
from typing import Any

PATCH_MARKER = "V7.6 DOCUMENT GRID AUTO OPEN V2"


def _renderer_context() -> tuple[str, int, str, Any] | None:
    """Return (kind, pid, subtype, currently_selected) for a known list renderer."""
    frame = inspect.currentframe()
    try:
        current = frame.f_back if frame else None
        for _ in range(28):
            if current is None:
                break
            name = current.f_code.co_name
            loc = current.f_locals
            if name in {"render_document_type", "_render_approval_document_type"}:
                pid = loc.get("pid")
                subtype = loc.get("doc_type")
                if pid not in (None, "") and subtype:
                    return "document", int(pid), str(subtype), loc.get("selected")
            if name in {"render_drawing_type", "_render_approval_shopdrawing_type"}:
                pid = loc.get("pid")
                subtype = loc.get("drawing_type")
                if pid not in (None, "") and subtype:
                    return "drawing", int(pid), str(subtype), loc.get("selected")
            current = current.f_back
    finally:
        del frame
        try:
            del current
        except Exception:
            pass
    return None


def _selected_record_id(result: Any) -> int | None:
    """Extract exactly one checked ID from the returned editor DataFrame."""
    try:
        if "Chọn" not in result.columns or "ID" not in result.columns:
            return None
        picked = result.loc[result["Chọn"] == True, "ID"].tolist()  # noqa: E712
        if len(picked) != 1:
            return None
        return int(picked[0])
    except Exception:
        return None


def install_document_selection_autopen() -> None:
    import streamlit as st

    if getattr(st, "_qlda_document_selection_autopen_installed", False):
        from qlda.presentation.streamlit.document_management_uniform_interaction import (
            install_document_management_uniform_interaction,
        )
        install_document_management_uniform_interaction()
        return

    original_data_editor = st.data_editor

    @wraps(original_data_editor)
    def data_editor_autopen(data, *args, **kwargs):
        result = original_data_editor(data, *args, **kwargs)

        key = str(kwargs.get("key") or "")
        is_doc_grid = key.startswith("doc_select_grid_")
        is_drawing_grid = key.startswith("drawing_select_grid_")
        if not (is_doc_grid or is_drawing_grid):
            return result

        rid = _selected_record_id(result)
        if rid is None:
            return result

        context = _renderer_context()
        if context is None:
            return result
        kind, pid, subtype, current_selected = context
        if (kind == "document") != is_doc_grid:
            return result

        try:
            current_id = int(current_selected) if current_selected not in (None, "") else None
        except Exception:
            current_id = None
        if current_id == rid:
            return result

        if kind == "document":
            select_key = f"doc_select_{pid}_{subtype}"
        else:
            select_key = f"drawing_select_{pid}_{subtype}"

        st.session_state[select_key + "_pending"] = int(rid)
        st.rerun()
        return result

    st.data_editor = data_editor_autopen
    st._qlda_document_selection_autopen_installed = True
    st._qlda_document_selection_autopen_marker = PATCH_MARKER

    from qlda.presentation.streamlit.document_management_uniform_interaction import (
        install_document_management_uniform_interaction,
    )
    install_document_management_uniform_interaction()


__all__ = ["install_document_selection_autopen"]
