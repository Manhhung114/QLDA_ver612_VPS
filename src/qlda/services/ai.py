from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Any, Sequence

from qlda.shared.legacy import load_module


class AIApplicationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "ai_error",
        retryable: bool = False,
        action: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.retryable = bool(retryable)
        self.action = action


_BOOTSTRAP_LOCK = Lock()
_BOOTSTRAPPED = False


def _bootstrap_ai_runtime() -> None:
    """Install the same non-UI AI compatibility stack used by production."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return
        from qlda.services.access import _bootstrap_access_policy

        _bootstrap_access_policy()
        installers = (
            ("runtime_settings_bridge_v622", "install_runtime_settings_bridge"),
            ("gemini_resilience_v622", "install_gemini_resilience"),
            ("ai_live_context_v622", "install_ai_live_context"),
            ("ai_claim_context_v622", "install_ai_claim_context"),
            ("ai_vo_context_v622", "install_ai_vo_context"),
            ("boq_cost_components_v622", "install_boq_cost_components"),
            ("boq_claim_terms_v622", "install_boq_claim_terms"),
            ("boq_claim_price_recovery_v622", "install_boq_claim_price_recovery"),
            ("boq_claim_price_header_guard_v622", "install_boq_claim_price_header_guard"),
            ("boq_ai_fullscan_v622", "install_boq_ai_fullscan"),
            ("claim_component_fullscan_v622", "install_claim_component_fullscan"),
            ("claim_material_period_guard_v622", "install_claim_material_period_guard"),
            ("project_remaining_components_v622", "install_project_remaining_components"),
        )
        for module_name, function_name in installers:
            getattr(load_module(module_name), function_name)()

        access = load_module("contractor_access_control_v622")
        access.capture_single_contractor_ai_context()
        load_module("contractor_ai_context_v622").install_contractor_ai_context()
        access.install_ai_access_guard()
        _BOOTSTRAPPED = True


class AIService:
    """Transport-neutral AI application service for FastAPI and future clients."""

    @staticmethod
    def _db_label() -> Path:
        return Path(
            os.environ.get(
                "QLDA_AI_DB_LABEL",
                os.environ.get("QLDA_WORKER_DB_LABEL", "/opt/qlda/shared/qlda-ai.db"),
            )
        )

    @classmethod
    def _assistant(cls, provider: str):
        _bootstrap_ai_runtime()
        legacy = load_module("ai_service")
        value = str(provider or "openai").strip().lower()
        if value in {"openai", "gpt"}:
            return legacy.OpenAIProjectAssistant(cls._db_label())
        if value in {"gemini", "google"}:
            return legacy.GeminiProjectAssistant(cls._db_label())
        raise ValueError("provider phải là openai hoặc gemini.")

    @staticmethod
    @contextmanager
    def _scope(workspace_scope: int | None):
        access = load_module("contractor_access_control_v622")
        access.set_ai_workspace_scope(workspace_scope)
        try:
            yield
        finally:
            access.set_ai_workspace_scope(None)

    @classmethod
    def _run(cls, provider: str, method: str, workspace_scope: int | None, *args: Any, **kwargs: Any):
        assistant = cls._assistant(provider)
        legacy = load_module("ai_service")
        try:
            with cls._scope(workspace_scope):
                return getattr(assistant, method)(*args, **kwargs)
        except legacy.AIServiceError as exc:
            raise AIApplicationError(
                str(exc),
                code=str(getattr(exc, "code", "ai_error") or "ai_error"),
                retryable=bool(getattr(exc, "retryable", False)),
                action=str(getattr(exc, "action", "") or ""),
            ) from exc

    @classmethod
    def ask(
        cls,
        project_id: int,
        question: str,
        *,
        provider: str = "openai",
        history: Sequence[dict[str, Any]] | None = None,
        status_date: date | None = None,
        use_web: bool | None = None,
        workspace_scope: int | None = None,
    ) -> str:
        return cls._run(
            provider, "ask_project", workspace_scope, int(project_id), question,
            history=history, status_date=status_date, use_web=use_web,
        )

    @classmethod
    def schedule_risk(
        cls, project_id: int, *, provider: str = "openai",
        status_date: date | None = None, workspace_scope: int | None = None,
    ) -> str:
        return cls._run(
            provider, "analyze_schedule_risk", workspace_scope, int(project_id),
            status_date=status_date,
        )

    @classmethod
    def draft_report(
        cls, project_id: int, *, provider: str = "openai", period: str = "tuần",
        status_date: date | None = None, workspace_scope: int | None = None,
    ) -> str:
        return cls._run(
            provider, "draft_report", workspace_scope, int(project_id),
            period=period, status_date=status_date,
        )

    @classmethod
    def legal_qa(
        cls, project_id: int, question: str, *, provider: str = "openai",
        status_date: date | None = None, use_web: bool = True,
        workspace_scope: int | None = None,
    ) -> str:
        return cls._run(
            provider, "legal_qa", workspace_scope, int(project_id), question,
            status_date=status_date, use_web=use_web,
        )

    @classmethod
    def test_connection(cls, *, provider: str = "openai") -> str:
        return cls._run(provider, "test_connection", None)
