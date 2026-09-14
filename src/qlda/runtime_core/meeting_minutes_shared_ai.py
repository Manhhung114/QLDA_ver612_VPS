from __future__ import annotations

"""Bridge Biên bản họp to the existing Công cụ -> Trợ lý AI.

This module deliberately does not create an AI client, provider configuration,
or a second chat history. It queues the review request into the same assistant
input used by ``render_ai_assistant`` and redirects the V7 navigation there.
"""

from typing import Any

from qlda.runtime_core import meeting_minutes_simple as meeting


_SHARED_AI_PREFIX = "qlda_shared_ai_pending_"
_NAV_PENDING_PREFIX = "qlda_shared_ai_nav_pending_"


def _target_ai_pid(app_globals: dict[str, Any], pid: int) -> int:
    master_pid = int(app_globals.get("_master_pid") or pid)
    can_view_all = bool(app_globals.get("_v622_can_view_all_contractors", False))
    return master_pid if can_view_all else int(pid)


def _queue_review(st, app_globals: dict[str, Any], pid: int, record: Any) -> None:
    ai_pid = _target_ai_pid(app_globals, pid)
    master_pid = int(app_globals.get("_master_pid") or pid)
    st.session_state[f"{_SHARED_AI_PREFIX}{ai_pid}"] = meeting._review_prompt(record)
    st.session_state[f"{_NAV_PENDING_PREFIX}{master_pid}"] = True
    st.rerun()


def _install_renderer_bridge(st) -> None:
    if getattr(meeting, "_qlda_shared_ai_renderer_installed", False):
        return

    original_render = meeting.render_meeting_minutes_simple

    def _shared_render(st_obj, app_globals: dict[str, Any], pid: int):
        original_button = st_obj.button

        def _button(label, *args, **kwargs):
            if str(label) == "🤖 Rà soát nội dung bằng AI":
                clicked = original_button(
                    "🤖 Rà soát bằng Trợ lý AI",
                    *args,
                    **kwargs,
                )
                if clicked:
                    key = str(kwargs.get("key") or "")
                    try:
                        selected = int(key.rsplit("_", 1)[-1])
                    except Exception:
                        selected = 0
                    db = app_globals.get("db")
                    record = db.document(selected) if db is not None and selected else None
                    if record is None:
                        st_obj.error("Không đọc được biên bản để gửi sang Trợ lý AI.")
                    else:
                        _queue_review(st_obj, app_globals, int(pid), record)
                # Never return True to the legacy private AI branch.
                return False
            return original_button(label, *args, **kwargs)

        st_obj.button = _button
        try:
            return original_render(st_obj, app_globals, int(pid))
        finally:
            st_obj.button = original_button

    meeting.render_meeting_minutes_simple = _shared_render

    def _disabled_private_ai(*_args, **_kwargs):
        raise RuntimeError("Biên bản họp dùng chung Trợ lý AI tại Công cụ; không tạo AI riêng.")

    # Safety guard: even if legacy code is called accidentally, a second AI
    # client/provider can no longer be created from the meeting-minutes module.
    meeting._build_ai = _disabled_private_ai
    meeting._qlda_shared_ai_renderer_installed = True


def install_meeting_minutes_shared_ai() -> None:
    """Install the shared-assistant queue, navigation and input bridge."""
    import streamlit as st

    if getattr(st, "_qlda_meeting_minutes_shared_ai_installed", False):
        return

    # Keep the simple archive renderer/status-free behavior from the previous
    # patch, then replace only its AI action with the central assistant bridge.
    meeting.install_meeting_minutes_simple_ui()
    _install_renderer_bridge(st)

    original_radio = st.radio
    original_chat_input = st.chat_input

    def _radio(label, options, *args, **kwargs):
        key = str(kwargs.get("key") or "")
        if key.startswith("qlda_v7_group_"):
            try:
                master_pid = int(key.rsplit("_", 1)[-1])
            except Exception:
                master_pid = 0
            nav_key = f"{_NAV_PENDING_PREFIX}{master_pid}"
            if master_pid and st.session_state.get(nav_key):
                st.session_state[key] = "📚 Công cụ"
        elif key.startswith("qlda_v7_section_") and key.endswith("_📚 Công cụ"):
            try:
                suffix = key[len("qlda_v7_section_"):]
                master_pid = int(suffix.split("_", 1)[0])
            except Exception:
                master_pid = 0
            nav_key = f"{_NAV_PENDING_PREFIX}{master_pid}"
            if master_pid and st.session_state.get(nav_key):
                st.session_state[key] = "🤖 Trợ lý AI"
                st.session_state.pop(nav_key, None)
        return original_radio(label, options, *args, **kwargs)

    def _chat_input(placeholder="", *args, **kwargs):
        key = str(kwargs.get("key") or "")
        if key.startswith("ai_chat_"):
            try:
                ai_pid = int(key.rsplit("_", 1)[-1])
            except Exception:
                ai_pid = 0
            pending_key = f"{_SHARED_AI_PREFIX}{ai_pid}"
            pending = st.session_state.pop(pending_key, None) if ai_pid else None
            if pending:
                return str(pending)
        return original_chat_input(placeholder, *args, **kwargs)

    st._qlda_meeting_minutes_shared_ai_original_radio = original_radio
    st._qlda_meeting_minutes_shared_ai_original_chat_input = original_chat_input
    st.radio = _radio
    st.chat_input = _chat_input
    st._qlda_meeting_minutes_shared_ai_installed = True
