from __future__ import annotations

"""Compatibility shim for the AI Supervisor navigation installer.

The implementation moved to ``qlda.presentation.streamlit``.  Keep this import
path temporarily because the V7.6 runtime feature registry is a compatibility
surface used by deployed workers/tests.  No UI/business logic may be added here.
"""

from qlda.presentation.streamlit.ai_supervisor_navigation import (
    AI_NAV_LABEL,
    HOME_NAV_LABEL,
    NAV_RADIO_LABEL,
    PATCH_MARKER,
    _inject_ai_nav_option,
    _is_main_navigation,
    install_ai_supervisor_navigation,
)

__all__ = [
    "AI_NAV_LABEL",
    "HOME_NAV_LABEL",
    "NAV_RADIO_LABEL",
    "PATCH_MARKER",
    "_inject_ai_nav_option",
    "_is_main_navigation",
    "install_ai_supervisor_navigation",
]
