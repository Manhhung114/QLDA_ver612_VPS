from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class AIProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str = "ai_error", retryable: bool = False, action: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.action = action


class LegacyAIProvider:
    """Infrastructure-only compatibility adapter for the packaged provider engine.

    New application/autonomy code must depend on application AI ports, never on
    ``runtime_core.ai_service`` directly. This adapter is the shrinking strangler
    boundary until the OpenAI/Gemini engines are fully native infrastructure.
    """

    @staticmethod
    def db_label() -> Path:
        return Path(
            os.environ.get(
                "QLDA_AI_DB_LABEL",
                os.environ.get("QLDA_WORKER_DB_LABEL", "/opt/qlda/shared/qlda-ai.db"),
            )
        )

    @staticmethod
    def _modules():
        from qlda.runtime_core import ai_service as engine
        from qlda.runtime_core import contractor_access_control as access
        from qlda.runtime_core.bootstrap import initialize_ai_runtime

        initialize_ai_runtime()
        return engine, access

    @classmethod
    def assistant(cls, provider: str):
        engine, _access = cls._modules()
        value = str(provider or "openai").strip().lower()
        if value in {"openai", "gpt"}:
            return engine.OpenAIProjectAssistant(cls.db_label())
        if value in {"gemini", "google"}:
            return engine.GeminiProjectAssistant(cls.db_label())
        raise ValueError("provider phải là openai hoặc gemini.")

    @classmethod
    @contextmanager
    def scope(cls, workspace_scope: int | None):
        _engine, access = cls._modules()
        access.set_ai_workspace_scope(workspace_scope)
        try:
            yield
        finally:
            access.set_ai_workspace_scope(None)

    @classmethod
    def run(
        cls,
        provider: str,
        method: str,
        workspace_scope: int | None,
        *args: Any,
        **kwargs: Any,
    ):
        engine, _access = cls._modules()
        assistant = cls.assistant(provider)
        try:
            with cls.scope(workspace_scope):
                return getattr(assistant, method)(*args, **kwargs)
        except engine.AIServiceError as exc:
            raise AIProviderError(
                str(exc),
                code=str(getattr(exc, "code", "ai_error") or "ai_error"),
                retryable=bool(getattr(exc, "retryable", False)),
                action=str(getattr(exc, "action", "") or ""),
            ) from exc


__all__ = ["LegacyAIProvider", "AIProviderError"]
