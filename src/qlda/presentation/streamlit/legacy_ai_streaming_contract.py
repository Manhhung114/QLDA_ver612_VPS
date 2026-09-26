from __future__ import annotations

"""Thin UI contract routing the source-controlled Streamlit chat to native AI.

The Streamlit shell still instantiates the historical assistant classes, but
project data retrieval and provider execution now go through the native V7
PostgreSQL/Data-Hub boundary. No legacy ``ProjectContextBuilder`` snapshot or
``runtime_core.ai_streaming`` execution path is used here.
"""

import re
from typing import Iterable


def _provider_name(assistant) -> str:
    return "gemini" if "gemini" in assistant.__class__.__name__.lower() else "openai"


def _project_wide_allowed() -> bool:
    """Return True only for a management identity, never for CONTRACTOR users."""
    try:
        import streamlit as st

        identity = dict(st.session_state.get("qlda_drive_identity") or {})
    except Exception:
        identity = {}
    approval = str(identity.get("approval_role") or identity.get("approval_group") or "").strip().upper()
    approval = approval.replace("-", "_").replace(" ", "_")
    if approval in {"CONTRACTOR", "NHA_THAU", "NHÀ_THẦU"}:
        return False
    role = str(identity.get("role") or "").strip().lower()
    if role == "admin":
        return True
    return approval in {
        "PROJECT_MANAGEMENT",
        "BQLDA",
        "SITE_MANAGEMENT",
        "CONSULTANT",
        "TVGS",
        "PROJECT_VIEWER",
    }


def _display_chunks(text: str) -> Iterable[str]:
    """Keep the existing Streamlit incremental display without legacy AI runtime."""
    value = str(text or "").strip()
    if not value:
        return
    parts = re.findall(r"\S+\s*", value)
    for start in range(0, len(parts), 6):
        yield "".join(parts[start : start + 6])


def _ask_project_stream(
    self,
    project_id,
    question,
    history=None,
    status_date=None,
    use_web=None,
) -> Iterable[str]:
    from qlda.infrastructure.ai.project_chat_complete import ask_project_chat
    from qlda.runtime_core.contractor_access_control import current_ai_workspace_scope

    workspace_scope = current_ai_workspace_scope() or int(project_id)
    answer = ask_project_chat(
        int(project_id),
        str(question or ""),
        provider=_provider_name(self),
        history=history,
        status_date=status_date,
        use_web=use_web,
        workspace_scope=int(workspace_scope),
        allow_project_wide=_project_wide_allowed(),
    )
    yield from _display_chunks(answer)


def install_legacy_ai_streaming_contract() -> None:
    """Keep only the historical method name; execution is fully native."""
    from qlda.runtime_core.ai_service import GeminiProjectAssistant, OpenAIProjectAssistant

    # Assign deliberately even if a historical runtime installer populated the
    # method earlier. Production chat must always use the native live-data path.
    for assistant_cls in (OpenAIProjectAssistant, GeminiProjectAssistant):
        assistant_cls.ask_project_stream = _ask_project_stream


__all__ = ["install_legacy_ai_streaming_contract"]
