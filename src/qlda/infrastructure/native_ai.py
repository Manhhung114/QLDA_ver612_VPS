from __future__ import annotations

"""Native AI adapter with contractor-tenant isolation and optional grounded RAG.

Application callers no longer import ``runtime_core.ai_service`` through this
module. The remaining legacy provider dependency is isolated behind
``qlda.infrastructure.ai.legacy_provider`` and can be removed independently.
"""

import os
import time
from datetime import date
from typing import Any, Sequence

from qlda.application.ai import AIContextService
from qlda.domain.errors import AIApplicationError
from qlda.infrastructure.ai.embeddings import ProviderEmbeddingAdapter
from qlda.infrastructure.ai.legacy_provider import AIProviderError, LegacyAIProvider
from qlda.infrastructure.ai.telemetry import content_hash, record_ai_event
from qlda.infrastructure.ai.vector_store import PostgresVectorContextStore


class NativeAIAdapter:
    """AIPort implementation backed by native infrastructure boundaries."""

    @staticmethod
    def _domain_error(exc: BaseException) -> AIApplicationError:
        return AIApplicationError(
            str(exc),
            code=str(getattr(exc, "code", "ai_error") or "ai_error"),
            retryable=bool(getattr(exc, "retryable", False)),
            action=str(getattr(exc, "action", "") or ""),
        )

    @staticmethod
    def _tenant(project_id: int, workspace_scope: int | None) -> int:
        value = int(workspace_scope or project_id or 0)
        if value <= 0:
            raise ValueError("AI cần workspace_project_id hợp lệ.")
        return value

    @staticmethod
    def _rag_enabled() -> bool:
        return str(os.environ.get("QLDA_AI_RAG_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def _ground_question(cls, tenant: int, question: str, provider: str) -> tuple[str, list[str]]:
        if not cls._rag_enabled() or not str(question or "").strip():
            return str(question or ""), []
        try:
            embedder = ProviderEmbeddingAdapter(provider if provider in {"openai", "gemini", "google"} else None)
            store = PostgresVectorContextStore(embed=embedder.embed)
            service = AIContextService(
                store,
                max_chars=max(2000, min(int(os.environ.get("QLDA_AI_RAG_MAX_CHARS", "18000")), 50000)),
            )
            top_k = max(1, min(int(os.environ.get("QLDA_AI_RAG_TOP_K", "8")), 20))
            context_text, bundle = service.build_grounded_context(tenant, question, top_k=top_k)
            if not context_text:
                return str(question or ""), []
            grounded = (
                f"{question}\n\n"
                "NGỮ CẢNH TRUY XUẤT CỦA ĐÚNG WORKSPACE NHÀ THẦU:\n"
                f"{context_text}\n\n"
                "YÊU CẦU NGUỒN: chỉ dùng phần ngữ cảnh trên khi phù hợp; khi dựa vào một đoạn, "
                "hãy nêu lại nhãn [NGUỒN n: ...]. Không suy đoán dữ liệu không có trong nguồn."
            )
            return grounded, list(bundle.citations)
        except Exception:
            # RAG is an augmentation layer; provider chat remains available when
            # pgvector/embedding/indexing is temporarily unavailable.
            return str(question or ""), []

    @classmethod
    def _run(
        cls,
        provider: str,
        method: str,
        workspace_scope: int | None,
        *args: Any,
        **kwargs: Any,
    ):
        try:
            return LegacyAIProvider.run(provider, method, workspace_scope, *args, **kwargs)
        except AIProviderError as exc:
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
        tenant = self._tenant(project_id, workspace_scope)
        grounded, source_refs = self._ground_question(tenant, str(question or ""), str(provider or "openai").lower())
        started = time.perf_counter()
        try:
            result = self._run(
                provider,
                "ask_project",
                tenant,
                int(project_id),
                grounded,
                history=history,
                status_date=status_date,
                use_web=use_web,
            )
            record_ai_event({
                "workspace_project_id": tenant,
                "event_type": "AI_CHAT",
                "provider": provider,
                "input": question,
                "input_hash": content_hash(question),
                "context": {"rag_sources": source_refs, "history_items": len(history or [])},
                "source_refs": source_refs,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "success": True,
            })
            return result
        except Exception as exc:
            record_ai_event({
                "workspace_project_id": tenant,
                "event_type": "AI_CHAT",
                "provider": provider,
                "input": question,
                "source_refs": source_refs,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "success": False,
                "error_code": str(getattr(exc, "code", exc.__class__.__name__)),
            })
            raise

    def schedule_risk(
        self,
        project_id: int,
        *,
        provider: str = "openai",
        status_date: date | None = None,
        workspace_scope: int | None = None,
    ) -> str:
        tenant = self._tenant(project_id, workspace_scope)
        return self._run(
            provider,
            "analyze_schedule_risk",
            tenant,
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
        tenant = self._tenant(project_id, workspace_scope)
        return self._run(
            provider,
            "draft_report",
            tenant,
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
        tenant = self._tenant(project_id, workspace_scope)
        grounded, source_refs = self._ground_question(tenant, str(question or ""), str(provider or "openai").lower())
        result = self._run(
            provider,
            "legal_qa",
            tenant,
            int(project_id),
            grounded,
            status_date=status_date,
            use_web=use_web,
        )
        record_ai_event({
            "workspace_project_id": tenant,
            "event_type": "AI_LEGAL_QA",
            "provider": provider,
            "input": question,
            "source_refs": source_refs,
            "success": True,
        })
        return result

    def test_connection(self, *, provider: str = "openai") -> str:
        return self._run(provider, "test_connection", None)
