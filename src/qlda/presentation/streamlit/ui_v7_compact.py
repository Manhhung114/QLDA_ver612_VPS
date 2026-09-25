from __future__ import annotations

# Presentation owner for the compact V7 Streamlit shell.
# The implementation is temporarily re-exported from the compatibility module
# while callers are migrated; no new business logic belongs here.
from qlda.runtime_core.ui_v7_compact import (  # noqa: F401
    install_caption_policy_v7,
    install_meeting_minutes_sheet_v7,
    install_theme_v7,
    install_vn_datetime_policy_v7,
    render_admin_caption_toggle_v7,
    render_header_v7,
    render_overview_v7,
)

__all__ = [
    "install_caption_policy_v7",
    "install_meeting_minutes_sheet_v7",
    "install_theme_v7",
    "install_vn_datetime_policy_v7",
    "render_admin_caption_toggle_v7",
    "render_header_v7",
    "render_overview_v7",
]
