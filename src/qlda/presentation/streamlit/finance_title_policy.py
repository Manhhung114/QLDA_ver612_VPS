from __future__ import annotations

"""Presentation-only finance heading policy.

The finance composition is owned elsewhere; this module only normalizes the
user-facing Streamlit heading. Keeping this behavior in the presentation layer
prevents UI monkey-patches from accumulating in ``runtime_core``.
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
