from __future__ import annotations

"""UI compatibility contract for the legacy project-assistant classes.

The V7 AI runtime no longer installs an AI stage or provider monkey patches.
The source-controlled Streamlit shell still invokes ``ask_project_stream`` on
``OpenAIProjectAssistant`` / ``GeminiProjectAssistant`` while that screen is
being migrated to the native AI boundary. This adapter restores only that UI
method contract; it does not install provider settings, retrieval, tools or any
``RuntimeStage.AI`` compatibility.
"""

from typing import Iterable


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


def install_legacy_ai_streaming_contract() -> None:
    from qlda.runtime_core.ai_service import GeminiProjectAssistant, OpenAIProjectAssistant

    for assistant_cls in (OpenAIProjectAssistant, GeminiProjectAssistant):
        if not callable(getattr(assistant_cls, "ask_project_stream", None)):
            assistant_cls.ask_project_stream = _ask_project_stream


__all__ = ["install_legacy_ai_streaming_contract"]
