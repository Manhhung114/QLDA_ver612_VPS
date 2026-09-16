from __future__ import annotations

"""Minimal finance heading policy used by Cleanup V2.

The six-sheet finance composition is owned by ``project_cost_management``.
``finance_management_ui`` remains the renderer for unpaid IPC cash planning, but
its older ``st.tabs`` wrapper is no longer installed because that duplicated the
same navigation concern.  This module keeps only the user-facing title rename.
"""

from functools import wraps

PATCH_MARKER = "V7.6 CLEANUP V2 FINANCE TITLE POLICY"


def install_finance_title_policy() -> None:
    import streamlit as st

    if getattr(st, "_qlda_finance_title_policy_installed", False):
        return

    previous_subheader = st.subheader

    @wraps(previous_subheader)
    def finance_subheader(body, *args, **kwargs):
        if str(body or "").strip() == "💰 Quản lý chi phí":
            body = "💰 Quản lý Tài chính"
        return previous_subheader(body, *args, **kwargs)

    st.subheader = finance_subheader
    st._qlda_finance_title_policy_installed = True
    st._qlda_finance_title_policy_marker = PATCH_MARKER


__all__ = ["PATCH_MARKER", "install_finance_title_policy"]
