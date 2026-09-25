from __future__ import annotations

"""UI compatibility contract for the legacy project-assistant classes.

The V7 AI runtime no longer installs an AI stage or provider monkey patches.
The source-controlled Streamlit shell still invokes ``ask_project_stream`` on
``OpenAIProjectAssistant`` / ``GeminiProjectAssistant`` while that screen is
being migrated to the native AI boundary. This adapter restores only the UI
contracts still required by that screen; it does not install provider settings,
retrieval, tools or any ``RuntimeStage.AI`` compatibility.
"""

from typing import Iterable


_LEGACY_LEGAL_MODULE_NAME = "qlda.runtime_core.legal_documents"
_LEGAL_TABLE_NAME = "legal_documents"


def _ask_project_stream(
    self,
    project_id,
    question,
    history=None,
    status_date=None,
    use_web=None,
) -> Iterable[str]:
    # Reuse the existing provider-specific streaming implementation without
    # re-enabling the retired AI bootstrap stage.
    from qlda.runtime_core.ai_streaming import _ask_project_stream as _stream

    yield from _stream(
        self,
        project_id,
        question,
        history=history,
        status_date=status_date,
        use_web=use_web,
    )


def _install_legacy_legal_table_alias() -> None:
    """Normalize one historical module-qualified table name used by project chat.

    ``ProjectContextBuilder._legal`` in the legacy assistant still asks
    ``table_exists`` for ``qlda.runtime_core.legal_documents``. SQLite tolerated
    that path indirectly in old deployments, but the PostgreSQL identifier guard
    correctly rejects dots. Until the Streamlit chat screen is fully migrated to
    the native AI context boundary, translate only this exact historical alias to
    the real SQL table name ``legal_documents``.
    """
    from qlda.runtime_core.ai_service import ProjectContextBuilder

    current = ProjectContextBuilder.table_exists
    if getattr(current, "_v7_legal_table_alias", False):
        return

    def table_exists_compat(self, connection, table: str) -> bool:
        normalized = str(table or "")
        if normalized == _LEGACY_LEGAL_MODULE_NAME:
            normalized = _LEGAL_TABLE_NAME
        return bool(current(self, connection, normalized))

    table_exists_compat._v7_legal_table_alias = True
    ProjectContextBuilder.table_exists = table_exists_compat


def install_legacy_ai_streaming_contract() -> None:
    from qlda.runtime_core.ai_service import GeminiProjectAssistant, OpenAIProjectAssistant

    _install_legacy_legal_table_alias()
    for assistant_cls in (OpenAIProjectAssistant, GeminiProjectAssistant):
        if not callable(getattr(assistant_cls, "ask_project_stream", None)):
            assistant_cls.ask_project_stream = _ask_project_stream


__all__ = ["install_legacy_ai_streaming_contract"]
