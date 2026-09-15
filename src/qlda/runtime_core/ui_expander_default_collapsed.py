from __future__ import annotations

"""Global Streamlit expander policy for QLDA.

All expandable sections start collapsed when a page is opened. Users can still
open any section normally by clicking its header. This intentionally overrides
legacy calls that requested ``expanded=True`` while preserving Streamlit's normal
runtime interaction/state after the section has been opened by the user.
"""

from functools import wraps

PATCH_MARKER = "V7.6 UI EXPANDERS DEFAULT COLLAPSED V1"


def install_expanders_default_collapsed() -> None:
    import streamlit as st

    if getattr(st, "_qlda_expanders_default_collapsed_installed", False):
        return

    original_expander = st.expander

    @wraps(original_expander)
    def expander_collapsed(label, *args, **kwargs):
        # Streamlit signature accepts expanded as the first positional argument
        # after label or as a keyword. Force only the initial/default request to
        # collapsed; the user can expand the rendered section normally.
        args_list = list(args)
        if args_list and isinstance(args_list[0], bool):
            args_list[0] = False
        if kwargs.get("expanded") is True:
            kwargs["expanded"] = False
        return original_expander(label, *args_list, **kwargs)

    st.expander = expander_collapsed
    st._qlda_expanders_default_collapsed_installed = True
    st._qlda_expanders_default_collapsed_marker = PATCH_MARKER


__all__ = ["install_expanders_default_collapsed"]
