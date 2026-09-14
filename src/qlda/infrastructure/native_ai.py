from __future__ import annotations

"""V7.6 native AI infrastructure adapter.

AI provider selection, workspace scoping and domain error translation are handled
through packaged production modules under ``src/qlda``. There is no repository-root
loader, qlda.runtime dependency or versioned module lookup in this adapter.
"""

import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from qlda.domain.errors import AIApplicationError
from qlda.runtime_core import ai_service as engine
from qlda.runtime_core import contractor_access_control as access
from qlda.runtime_core.bootstrap import initialize_ai_runtime


class NativeAIAdapter:
    """AIPort implementation backed by packaged OpenAI/Gemini engines."""

    @staticmethod
    def _db_label() -> Path:
        return Path(
            os.environ.get(
                "QLDA_AI_DB_LABEL",
                os.environ.get("QLDA_WORKER_DB_LABEL", "/opt/qlda/shared/qlda-ai.db"),
            )
        )

    @staticmethod
    def _domain_error(exc: BaseException) -> AIApplicationError:
        return AIApplicationError(
            str(exc),
            code=str(getattr(exc, "code", "ai_error") or "ai_error"),
            retryable=bool(getattr(exc, "retryable", False)),
            action=str(getattr(exc, "action", "") or ""),
        )

    @classmethod
    def _assistant(cls, provider: str):
        initialize_ai_runtime()
        value = str(provider or "openai").strip().lower()
        if value in {"openai", "gpt"}:
            return engine.OpenAIProjectAssistant(cls._db_label())
        if value in {"gemini", "google"}:
            return engine.GeminiProjectAssistant(cls._db_label())
        raise ValueError("provider phải là openai hoặc gemini.")

    @staticmethod
    @contextmanager
    def _scope(workspace_scope: int | None):
        access.set_ai_workspace_scope(workspace_scope)
        try:
            yield
        finally:
            access.set_ai_workspace_scope(None)

    @classmethod
    def _run(
        cls,
        provider: str,
        method: str,
        workspace_scope: int | None,
        *args: Any,
        **kwargs: Any,
    ):
        assistant = cls._assistant(provider)
        try:
            with cls._scope(workspace_scope):
                return getattr(assistant, method)(*args, **kwargs)
        except engine.AIServiceError as exc:
            raise cls._domain_error(exc) from exc

    def ask(
        self,
        project_id: int,
        question: str,
        *,
        provider: str = "openai",
        history: Sequence[dict[str, Any]] | None = None,
        status_date: date | None = None,
        use_web: bool | None = None,
        workspace_scope: int | None = None,
    ) -> str:
        return self._run(
            provider,
            "ask_project",
            workspace_scope,
            int(project_id),
            question,
            history=history,
            status_date=status_date,
            use_web=use_web,
        )

    def schedule_risk(
        self,
        project_id: int,
        *,
        provider: str = "openai",
        status_date: date | None = None,
        workspace_scope: int | None = None,
    ) -> str:
        return self._run(
            provider,
            "analyze_schedule_risk",
            workspace_scope,
            int(project_id),
            status_date=status_date,
        )

    def draft_report(
        self,
        project_id: int,
        *,
        provider: str = "openai",
        period: str = "tuần",
        status_date: date | None = None,
        workspace_scope: int | None = None,
    ) -> str:
        return self._run(
            provider,
            "draft_report",
            workspace_scope,
            int(project_id),
            period=period,
            status_date=status_date,
        )

    def legal_qa(
        self,
        project_id: int,
        question: str,
        *,
        provider: str = "openai",
        status_date: date | None = None,
        use_web: bool = True,
        workspace_scope: int | None = None,
    ) -> str:
        return self._run(
            provider,
            "legal_qa",
            workspace_scope,
            int(project_id),
            question,
            status_date=status_date,
            use_web=use_web,
        )

    def test_connection(self, *, provider: str = "openai") -> str:
        return self._run(provider, "test_connection", None)
