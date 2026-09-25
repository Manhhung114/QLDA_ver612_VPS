from __future__ import annotations

"""Native AI adapter with tenant isolation, provenance, RAG and audit.

No import from ``qlda.runtime_core`` is allowed in this module. Provider SDK
execution is owned by ``qlda.infrastructure.ai.provider_engine``.
"""

import os
import time
from datetime import date
from typing import Any, Sequence

from qlda.application.ai import AIContextService
from qlda.domain.errors import AIApplicationError
from qlda.infrastructure.ai.document_ingestion import (
    build_document_chunks,
    detect_mime,
    extract_document_text,
)
from qlda.infrastructure.ai.embeddings import ProviderEmbeddingAdapter
from qlda.infrastructure.ai.provider_engine import NativeProviderEngine, NativeProviderError
from qlda.infrastructure.ai.telemetry import content_hash, record_ai_event
from qlda.infrastructure.ai.vector_store import PostgresVectorContextStore
from qlda.infrastructure.ai.workspace_indexer import sync_workspace_sources


class NativeAIAdapter:
    """AIPort implementation backed only by native infrastructure boundaries."""

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
        if int(project_id or value) != value:
            raise ValueError("project_id phải trùng workspace_project_id của phiên AI hiện tại.")
        return value

    @staticmethod
    def _rag_enabled() -> bool:
        return str(os.environ.get("QLDA_AI_RAG_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _provider_name(provider: str) -> str:
        value = str(provider or "openai").strip().lower()
        return "gemini" if value in {"gemini", "google"} else "openai"

    @classmethod
    def _store(cls, provider: str):
        embedder = ProviderEmbeddingAdapter(cls._provider_name(provider))
        return PostgresVectorContextStore(embed=embedder.embed)

    @classmethod
    def _ground_question(cls, tenant: int, question: str, provider: str) -> tuple[str, list[str]]:
        if not cls._rag_enabled() or not str(question or "").strip():
            return str(question or ""), []
        try:
            store = cls._store(provider)
            try:
                sync_workspace_sources(store, tenant)
            except Exception:
                pass
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
                "YÊU CẦU NGUỒN: chỉ dùng ngữ cảnh trên khi phù hợp; khi dựa vào một đoạn, "
                "hãy giữ nhãn [NGUỒN n: ...]. Không suy đoán dữ liệu không có trong nguồn."
            )
            return grounded, list(bundle.citations)
        except Exception:
            # RAG augments provider chat; a temporary vector/embedding outage must
            # not make the assistant itself unavailable.
            return str(question or ""), []

    @classmethod
    def _complete(cls, provider: str, prompt: str, *, use_web: bool | None = None) -> str:
        try:
            return NativeProviderEngine.complete(cls._provider_name(provider), prompt, use_web=use_web)
        except NativeProviderError as exc:
            raise cls._domain_error(exc) from exc

    @staticmethod
    def _event(
        tenant: int,
        event_type: str,
        provider: str,
        started: float,
        *,
        input_text: str = "",
        source_refs: Sequence[str] | None = None,
        success: bool = True,
        error: BaseException | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        record_ai_event({
            "workspace_project_id": int(tenant),
            "event_type": str(event_type),
            "provider": str(provider),
            "input": str(input_text or ""),
            "input_hash": content_hash(input_text) if input_text else "",
            "context": context or {},
            "source_refs": list(source_refs or []),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "success": bool(success),
            "error_code": "" if error is None else str(getattr(error, "code", error.__class__.__name__)),
        })

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
        grounded, source_refs = self._ground_question(tenant, str(question or ""), provider)
        history_text = ""
        if history:
            items = list(history)[-10:]
            history_text = "\n\nHỘI THOẠI GẦN NHẤT:\n" + "\n".join(
                f"{str(item.get('role') or 'user').upper()}: {str(item.get('content') or '')[:1800]}"
                for item in items
            )
        prompt = (
            f"WORKSPACE_PROJECT_ID: {tenant}\n"
            f"NGÀY TRẠNG THÁI: {(status_date or date.today()).isoformat()}\n"
            f"CÂU HỎI: {grounded}{history_text}"
        )
        started = time.perf_counter()
        try:
            result = self._complete(provider, prompt, use_web=use_web)
            self._event(
                tenant, "AI_CHAT", provider, started,
                input_text=question, source_refs=source_refs,
                context={"history_items": len(history or []), "rag_source_count": len(source_refs)},
            )
            return result
        except Exception as exc:
            self._event(tenant, "AI_CHAT", provider, started, input_text=question, source_refs=source_refs, success=False, error=exc)
            raise

    def ask_stream(self, *args: Any, chunk_chars: int = 700, **kwargs: Any):
        """Stable streaming surface without binding the UI to provider SDK events."""
        text = self.ask(*args, **kwargs)
        size = max(120, min(int(chunk_chars), 4000))
        for start in range(0, len(text), size):
            yield text[start:start + size]

    def schedule_risk(
        self,
        project_id: int,
        *,
        provider: str = "openai",
        status_date: date | None = None,
        workspace_scope: int | None = None,
    ) -> str:
        tenant = self._tenant(project_id, workspace_scope)
        objective = (
            "Phân tích rủi ro tiến độ của dự án hiện tại. Ưu tiên công việc critical, quá hạn, "
            "chênh lệch planned/actual và hồ sơ có thể chặn thi công. Không tự bịa số liệu. "
            "Đưa ra: (1) rủi ro chính, (2) bằng chứng/mã tham chiếu, (3) hành động đề xuất, (4) dữ liệu còn thiếu."
        )
        grounded, refs = self._ground_question(tenant, objective, provider)
        prompt = f"WORKSPACE_PROJECT_ID: {tenant}\nNGÀY TRẠNG THÁI: {(status_date or date.today()).isoformat()}\n{grounded}"
        started = time.perf_counter()
        try:
            result = self._complete(provider, prompt, use_web=False)
            self._event(tenant, "AI_SCHEDULE_RISK", provider, started, input_text=objective, source_refs=refs)
            return result
        except Exception as exc:
            self._event(tenant, "AI_SCHEDULE_RISK", provider, started, input_text=objective, source_refs=refs, success=False, error=exc)
            raise

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
        objective = (
            f"Soạn dự thảo báo cáo {period} của dự án. Có các mục: tiến độ, hồ sơ/bản vẽ, chất lượng, "
            "chi phí/VO/IPC nếu có bằng chứng, rủi ro, việc cần quyết định và kế hoạch kỳ tới. "
            "Không tự tính hoặc suy đoán giá trị không có trong nguồn; giữ mã tham chiếu nguồn."
        )
        grounded, refs = self._ground_question(tenant, objective, provider)
        prompt = f"WORKSPACE_PROJECT_ID: {tenant}\nNGÀY BÁO CÁO: {(status_date or date.today()).isoformat()}\n{grounded}"
        started = time.perf_counter()
        try:
            result = self._complete(provider, prompt, use_web=False)
            self._event(tenant, "AI_DRAFT_REPORT", provider, started, input_text=objective, source_refs=refs)
            return result
        except Exception as exc:
            self._event(tenant, "AI_DRAFT_REPORT", provider, started, input_text=objective, source_refs=refs, success=False, error=exc)
            raise

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
        objective = (
            f"Câu hỏi pháp lý/tiêu chuẩn xây dựng: {question}\n"
            "Ưu tiên nguồn nội bộ có provenance. Nếu dùng web, ưu tiên nguồn chính thức; tách rõ dữ liệu nội bộ, "
            "nguồn web và phần chưa xác minh. Không kết luận pháp lý chỉ từ tiêu đề văn bản."
        )
        grounded, refs = self._ground_question(tenant, objective, provider)
        prompt = f"WORKSPACE_PROJECT_ID: {tenant}\nNGÀY TRA CỨU: {(status_date or date.today()).isoformat()}\n{grounded}"
        started = time.perf_counter()
        try:
            result = self._complete(provider, prompt, use_web=use_web)
            self._event(tenant, "AI_LEGAL_QA", provider, started, input_text=question, source_refs=refs)
            return result
        except Exception as exc:
            self._event(tenant, "AI_LEGAL_QA", provider, started, input_text=question, source_refs=refs, success=False, error=exc)
            raise

    def summarize_file(
        self,
        project_id: int,
        name: str,
        data: bytes,
        *,
        provider: str = "openai",
        instruction: str = "Tóm tắt nội dung chính, rủi ro và việc cần xử lý.",
        mime_type: str = "",
        status_date: date | None = None,
        workspace_scope: int | None = None,
    ) -> str:
        tenant = self._tenant(project_id, workspace_scope)
        raw = bytes(data or b"")
        mime = detect_mime(name, mime_type)
        started = time.perf_counter()
        refs: list[str] = []
        try:
            provenance, chunks = build_document_chunks(tenant, name, raw, mime_type=mime)
            refs = [provenance.source_ref]
            if chunks and self._rag_enabled():
                try:
                    self._store(provider).upsert_chunks(chunks)
                except Exception:
                    pass
            if mime.startswith("image/"):
                result = NativeProviderEngine.vision_text(
                    self._provider_name(provider), raw, mime,
                    f"WORKSPACE_PROJECT_ID: {tenant}\nTệp: {name}\nYêu cầu: {instruction}",
                )
            else:
                text = extract_document_text(name, raw, mime)
                if not text.strip():
                    raise AIApplicationError(
                        "Định dạng tệp chưa hỗ trợ trích xuất văn bản native.",
                        code="unsupported_document",
                        retryable=False,
                        action="Dùng PDF có text, DOCX, XLSX, CSV, TXT/MD hoặc ảnh.",
                    )
                limit = max(4000, min(int(os.environ.get("QLDA_AI_FILE_MAX_CHARS", "50000")), 120000))
                prompt = (
                    f"WORKSPACE_PROJECT_ID: {tenant}\nNGÀY: {(status_date or date.today()).isoformat()}\n"
                    f"[NGUỒN 1: {provenance.source_ref}]\nTÊN TỆP: {name}\n"
                    f"YÊU CẦU: {instruction}\n\nNỘI DUNG TRÍCH XUẤT:\n{text[:limit]}"
                )
                result = self._complete(provider, prompt, use_web=False)
            self._event(
                tenant, "AI_DOCUMENT_SUMMARY", provider, started,
                input_text=f"{name}: {instruction}", source_refs=refs,
                context={"mime_type": mime, "byte_size": len(raw), "chunk_count": len(chunks)},
            )
            return result
        except NativeProviderError as exc:
            err = self._domain_error(exc)
            self._event(tenant, "AI_DOCUMENT_SUMMARY", provider, started, input_text=name, source_refs=refs, success=False, error=err)
            raise err from exc
        except Exception as exc:
            self._event(tenant, "AI_DOCUMENT_SUMMARY", provider, started, input_text=name, source_refs=refs, success=False, error=exc)
            raise

    def test_connection(self, *, provider: str = "openai") -> str:
        try:
            return NativeProviderEngine.test_connection(self._provider_name(provider))
        except NativeProviderError as exc:
            raise self._domain_error(exc) from exc


__all__ = ["NativeAIAdapter"]
