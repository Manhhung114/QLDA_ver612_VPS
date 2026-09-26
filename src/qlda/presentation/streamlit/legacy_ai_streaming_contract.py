from __future__ import annotations

"""Presentation compatibility contract for the source-controlled Streamlit AI UI.

The Streamlit shell still instantiates the historical assistant classes. Every
project-data operation is therefore rebound at the presentation edge to the
currently authorized workspace. Provider execution for project chat remains in
the native V7 infrastructure boundary; legacy context helpers are scope-guarded
only for the remaining file/risk/report UI methods.
"""

import re
from typing import Iterable


_CONTRACTOR_ROLES = {"CONTRACTOR", "NHA_THAU", "NHÀ_THAU", "NHÀ_THẦU"}
_MANAGEMENT_ROLES = {
    "PROJECT_MANAGEMENT",
    "BQLDA",
    "SITE_MANAGEMENT",
    "CONSULTANT",
    "TVGS",
    "PROJECT_VIEWER",
    "ALL",
}


def _norm_role(value) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _provider_name(assistant) -> str:
    return "gemini" if "gemini" in assistant.__class__.__name__.lower() else "openai"


def _active_scope(project_id: int) -> tuple[int, str]:
    """Resolve the workspace published by the RBAC selector, fail-closed for contractors."""
    requested = int(project_id or 0)
    state = {}
    try:
        import streamlit as st

        state = st.session_state
    except Exception:
        state = {}

    try:
        active_master = int(state.get("qlda_active_master_project_id") or 0)
    except Exception:
        active_master = 0
    try:
        active_workspace = int(state.get("qlda_active_workspace_project_id") or 0)
    except Exception:
        active_workspace = 0
    effective_role = _norm_role(state.get("qlda_effective_approval_role") or "")

    same_project = requested <= 0 or requested in {active_master, active_workspace}
    if same_project and active_workspace > 0:
        return active_workspace, effective_role

    # A workspace pinned for another master project must never bleed into the
    # newly requested project. Contractors are blocked until the new project's
    # RBAC selector publishes its own workspace; management callers may continue
    # only with the requested project id and must re-resolve scope there.
    if active_master > 0 and requested > 0 and requested not in {active_master, active_workspace}:
        if effective_role in _CONTRACTOR_ROLES:
            raise PermissionError(
                "Phạm vi workspace hiện tại thuộc dự án khác. AI đã chặn truy vấn cho đến khi quyền workspace của dự án mới được xác nhận."
            )
        return requested, effective_role

    from qlda.runtime_core.contractor_access_control import current_ai_workspace_scope

    pinned = int(current_ai_workspace_scope() or 0)
    if pinned > 0:
        return pinned, effective_role

    if effective_role in _CONTRACTOR_ROLES:
        raise PermissionError(
            "Không xác định được workspace nhà thầu được cấp quyền. AI đã chặn truy vấn để tránh đọc nhầm dữ liệu dự án tổng."
        )
    return requested, effective_role


def _project_wide_allowed(project_id: int) -> bool:
    """Only management identities may explicitly widen native chat beyond the active workspace."""
    _, effective_role = _active_scope(project_id)
    if effective_role in _CONTRACTOR_ROLES:
        return False

    try:
        import streamlit as st

        identity = dict(st.session_state.get("qlda_drive_identity") or {})
    except Exception:
        identity = {}
    role = str(identity.get("role") or "").strip().lower()
    if role == "admin":
        return True
    if effective_role:
        return effective_role in _MANAGEMENT_ROLES
    approval = _norm_role(identity.get("approval_role") or identity.get("approval_group") or "")
    return approval in _MANAGEMENT_ROLES


def _display_chunks(text: str) -> Iterable[str]:
    """Keep the existing Streamlit incremental display without legacy AI runtime."""
    value = str(text or "").strip()
    if not value:
        return
    parts = re.findall(r"\S+\s*", value)
    for start in range(0, len(parts), 6):
        yield "".join(parts[start : start + 6])


def _ask_project(
    self,
    project_id,
    question,
    history=None,
    status_date=None,
    use_web=None,
) -> str:
    from qlda.infrastructure.ai.project_chat_complete import ask_project_chat

    workspace_scope, _ = _active_scope(int(project_id))
    return ask_project_chat(
        int(project_id),
        str(question or ""),
        provider=_provider_name(self),
        history=history,
        status_date=status_date,
        use_web=use_web,
        workspace_scope=int(workspace_scope),
        allow_project_wide=_project_wide_allowed(int(project_id)),
    )


def _ask_project_stream(
    self,
    project_id,
    question,
    history=None,
    status_date=None,
    use_web=None,
) -> Iterable[str]:
    answer = _ask_project(
        self,
        project_id,
        question,
        history=history,
        status_date=status_date,
        use_web=use_web,
    )
    yield from _display_chunks(answer)


def install_legacy_ai_streaming_contract() -> None:
    """Bind every legacy Streamlit assistant entry point to the authorized workspace."""
    from qlda.runtime_core.ai_service import (
        GeminiProjectAssistant,
        OpenAIProjectAssistant,
        ProjectContextBuilder,
    )

    # Project chat, risk analysis, draft reports and legal QA all eventually call
    # ask_project. Rebinding this method gives those tools the same native live
    # context and workspace authorization as streaming chat.
    for assistant_cls in (OpenAIProjectAssistant, GeminiProjectAssistant):
        assistant_cls.ask_project = _ask_project
        assistant_cls.ask_project_stream = _ask_project_stream

    # File analysis still uses the historical provider-specific upload helper.
    # Preserve it, but replace any master/default project id with the authorized
    # active workspace before its context snapshot is built.
    if not hasattr(OpenAIProjectAssistant, "_qlda_authorized_summarize_original"):
        OpenAIProjectAssistant._qlda_authorized_summarize_original = OpenAIProjectAssistant.summarize_file
    if "_qlda_authorized_summarize_original" not in GeminiProjectAssistant.__dict__:
        GeminiProjectAssistant._qlda_authorized_summarize_original = GeminiProjectAssistant.summarize_file

    openai_original = OpenAIProjectAssistant._qlda_authorized_summarize_original
    gemini_original = GeminiProjectAssistant.__dict__["_qlda_authorized_summarize_original"]

    def openai_summarize(self, project_id, filename, file_bytes, instruction="", status_date=None):
        workspace_id, _ = _active_scope(int(project_id))
        return openai_original(
            self, int(workspace_id), filename, file_bytes, instruction, status_date
        )

    def gemini_summarize(self, project_id, filename, file_bytes, instruction="", status_date=None):
        workspace_id, _ = _active_scope(int(project_id))
        return gemini_original(
            self, int(workspace_id), filename, file_bytes, instruction, status_date
        )

    OpenAIProjectAssistant.summarize_file = openai_summarize
    GeminiProjectAssistant.summarize_file = gemini_summarize

    # The attachment picker is created directly from ProjectContextBuilder in the
    # Streamlit shell, so guard the catalog separately. This prevents a master
    # project id from listing another contractor's files.
    if not hasattr(ProjectContextBuilder, "_qlda_authorized_catalog_original"):
        ProjectContextBuilder._qlda_authorized_catalog_original = ProjectContextBuilder.attachment_catalog
    catalog_original = ProjectContextBuilder._qlda_authorized_catalog_original

    def authorized_catalog(self, project_id: int):
        workspace_id, _ = _active_scope(int(project_id))
        return catalog_original(self, int(workspace_id))

    ProjectContextBuilder.attachment_catalog = authorized_catalog


__all__ = ["install_legacy_ai_streaming_contract"]
