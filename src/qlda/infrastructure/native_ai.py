from __future__ import annotations

"""Native V7.3 AI infrastructure adapter.

The clean application boundary talks directly to this adapter. The adapter owns
provider selection, AI runtime bootstrap, workspace scoping and domain error
translation. Proven root-level AI/context engines are loaded lazily as
compatibility engines; the deprecated ``qlda.services.ai`` and
``qlda.services.access`` facades are no longer part of the runtime path.
"""

import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Any, Sequence

from qlda.domain.errors import AIApplicationError
from qlda.runtime import legacy_import

_BOOTSTRAP_LOCK = Lock()
_BOOTSTRAPPED = False

_AI_INSTALLERS = (
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


def _compat(module_name: str):
    """Load one proven AI/runtime engine lazily from the repository root."""
    return legacy_import(module_name)


def _bootstrap_ai_runtime() -> None:
    """Install the non-UI AI compatibility stack exactly once per process."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return

        # Preserve the proven PostgreSQL-backed CloudDatabase compatibility
        # runtime used by the existing AI context builders. This happens only on
        # the first AI call, never while composing/importing the application.
        from qlda.infrastructure.database import make_database

        make_database()

        workspace = _compat("contractor_workspace_v622")
        access = _compat("contractor_access_control_v622")
        guard = _compat("default_workspace_admin_guard_v622")
        workspace.install_contractor_workspace()
        access.install_contractor_access_control()
        guard.install_default_workspace_admin_guard()

        for module_name, function_name in _AI_INSTALLERS:
            getattr(_compat(module_name), function_name)()

        # The order is intentional: capture the single-workspace methods before
        # the project-wide aggregate context patch, then apply the ContextVar
        # access guard last so contractor requests cannot leak other workspaces.
        access.capture_single_contractor_ai_context()
        _compat("contractor_ai_context_v622").install_contractor_ai_context()
        access.install_ai_access_guard()
        _BOOTSTRAPPED = True


class NativeAIAdapter:
    """AIPort implementation with no dependency on ``qlda.services``."""

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
        _bootstrap_ai_runtime()
        engine = _compat("ai_service")
        value = str(provider or "openai").strip().lower()
        if value in {"openai", "gpt"}:
            return engine.OpenAIProjectAssistant(cls._db_label())
        if value in {"gemini", "google"}:
            return engine.GeminiProjectAssistant(cls._db_label())
        raise ValueError("provider phải là openai hoặc gemini.")

    @staticmethod
    @contextmanager
    def _scope(workspace_scope: int | None):
        access = _compat("contractor_access_control_v622")
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
        engine = _compat("ai_service")
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
