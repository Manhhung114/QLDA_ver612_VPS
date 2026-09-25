from __future__ import annotations

"""Stable UI-facing AI surface implemented entirely on the native AI boundary.

The Streamlit application historically imported assistant classes from
``runtime_core.ai_service``. This module preserves the small presentation API
while moving ownership to infrastructure/native AI. It intentionally does not
import any ``qlda.runtime_core`` module.
"""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from qlda.domain.errors import AIApplicationError
from qlda.infrastructure.ai.provider_engine import _safe_error
from qlda.infrastructure.native_ai import NativeAIAdapter
from qlda.infrastructure.postgres import connect


AIServiceError = AIApplicationError


def _mapped_provider_error(exc: BaseException, provider: str) -> AIApplicationError:
    mapped = _safe_error(exc, provider)
    return AIApplicationError(
        str(mapped),
        code=str(getattr(mapped, "code", "ai_provider_error") or "ai_provider_error"),
        retryable=bool(getattr(mapped, "retryable", False)),
        action=str(getattr(mapped, "action", "") or ""),
    )


def openai_error_to_service_error(exc: BaseException) -> AIApplicationError:
    return _mapped_provider_error(exc, "openai")


def gemini_error_to_service_error(exc: BaseException) -> AIApplicationError:
    return _mapped_provider_error(exc, "gemini")


@dataclass(slots=True)
class AISettings:
    api_key: str = ""
    model: str = "gpt-5-mini"
    use_web: bool = False

    @classmethod
    def from_env(cls) -> "AISettings":
        return cls(
            api_key=str(os.environ.get("OPENAI_API_KEY") or "").strip(),
            model=str(os.environ.get("OPENAI_MODEL") or "gpt-5-mini").strip() or "gpt-5-mini",
            use_web=_env_bool("AI_WEB_SEARCH", _env_bool("OPENAI_WEB_SEARCH", False)),
        )


@dataclass(slots=True)
class GeminiSettings:
    api_key: str = ""
    model: str = "auto"
    use_web: bool = False

    @classmethod
    def from_env(cls) -> "GeminiSettings":
        return cls(
            api_key=str(os.environ.get("GEMINI_API_KEY") or "").strip(),
            model=str(os.environ.get("GEMINI_MODEL") or "auto").strip() or "auto",
            use_web=_env_bool("AI_WEB_SEARCH", _env_bool("GEMINI_WEB_SEARCH", False)),
        )


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    return bool(default) if not raw else raw in {"1", "true", "yes", "on"}


class _BaseProjectAssistant:
    provider = "openai"

    def __init__(self, db_path: str | Path, settings: AISettings | GeminiSettings):
        # db_path is retained in the presentation signature only. Native AI data
        # access is PostgreSQL/RAG based and does not open the legacy SQLite file.
        self.db_path = Path(db_path)
        self.settings = settings
        self._adapter = NativeAIAdapter()
        self._apply_process_settings()

    def _apply_process_settings(self) -> None:
        # Existing Streamlit settings are server-owned. Export them only to the
        # current process so the native provider engine consumes one source of truth.
        if self.provider == "gemini":
            if self.settings.api_key:
                os.environ["GEMINI_API_KEY"] = str(self.settings.api_key)
            if self.settings.model:
                os.environ["GEMINI_MODEL"] = str(self.settings.model)
        else:
            if self.settings.api_key:
                os.environ["OPENAI_API_KEY"] = str(self.settings.api_key)
            if self.settings.model:
                os.environ["OPENAI_MODEL"] = str(self.settings.model)
        os.environ["AI_WEB_SEARCH"] = "1" if bool(self.settings.use_web) else "0"

    def test_connection(self) -> str:
        return self._adapter.test_connection(provider=self.provider)

    def ask_project(
        self,
        project_id: int,
        question: str,
        history: Sequence[dict[str, Any]] | None = None,
        status_date: date | None = None,
        *,
        use_web: bool | None = None,
    ) -> str:
        return self._adapter.ask(
            int(project_id), question,
            provider=self.provider,
            history=history,
            status_date=status_date,
            use_web=self.settings.use_web if use_web is None else bool(use_web),
            workspace_scope=int(project_id),
        )

    def ask_project_stream(
        self,
        project_id: int,
        question: str,
        history: Sequence[dict[str, Any]] | None = None,
        status_date: date | None = None,
        *,
        use_web: bool | None = None,
    ):
        yield from self._adapter.ask_stream(
            int(project_id), question,
            provider=self.provider,
            history=history,
            status_date=status_date,
            use_web=self.settings.use_web if use_web is None else bool(use_web),
            workspace_scope=int(project_id),
        )

    def analyze_schedule_risk(self, project_id: int, status_date: date | None = None) -> str:
        return self._adapter.schedule_risk(
            int(project_id), provider=self.provider, status_date=status_date,
            workspace_scope=int(project_id),
        )

    def draft_report(self, project_id: int, period: str = "tuần", status_date: date | None = None) -> str:
        return self._adapter.draft_report(
            int(project_id), provider=self.provider, period=period, status_date=status_date,
            workspace_scope=int(project_id),
        )

    def legal_qa(
        self,
        project_id: int,
        question: str,
        status_date: date | None = None,
        *,
        use_web: bool = True,
    ) -> str:
        return self._adapter.legal_qa(
            int(project_id), question, provider=self.provider, status_date=status_date,
            use_web=bool(use_web), workspace_scope=int(project_id),
        )

    def summarize_file(
        self,
        project_id: int,
        file_name: str,
        data: bytes,
        instruction: str = "Tóm tắt nội dung chính, rủi ro và việc cần xử lý.",
        status_date: date | None = None,
    ) -> str:
        return self._adapter.summarize_file(
            int(project_id), file_name, bytes(data or b""), provider=self.provider,
            instruction=instruction, status_date=status_date,
            workspace_scope=int(project_id),
        )


class OpenAIProjectAssistant(_BaseProjectAssistant):
    provider = "openai"


class GeminiProjectAssistant(_BaseProjectAssistant):
    provider = "gemini"


class ProjectContextBuilder:
    """Narrow native document repository used by the existing Streamlit file picker.

    General AI context is supplied through Unified Context/RAG. This class only
    exposes attachment metadata/bytes needed by the presentation flow and uses
    PostgreSQL directly rather than the retired SQLite AI context builder.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else None

    @staticmethod
    def _columns(table: str) -> set[str]:
        try:
            with connect() as conn:
                rows = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name=%s",
                    (table,),
                ).fetchall()
            return {str(row.get("column_name") or "") for row in rows}
        except Exception:
            return set()

    def attachment_catalog(self, project_id: int) -> list[dict[str, Any]]:
        attachment_cols = self._columns("document_attachments")
        document_cols = self._columns("documents")
        if not attachment_cols or not document_cols or "document_id" not in attachment_cols:
            return []
        a_fields = [name for name in ("id", "document_id", "file_name", "mime_type", "drive_file_id", "storage_backend", "file_path") if name in attachment_cols]
        if "id" not in a_fields:
            return []
        d_fields = [name for name in ("doc_type", "code", "subject") if name in document_cols]
        select = [f'a."{name}" AS "{name}"' for name in a_fields]
        select.extend(f'd."{name}" AS "{name}"' for name in d_fields)
        if "file_content" in attachment_cols:
            select.append('octet_length(a."file_content") AS "blob_size"')
        else:
            select.append('0 AS "blob_size"')
        where = ["d.project_id=%s"] if "project_id" in document_cols else []
        params: list[Any] = [int(project_id)] if where else []
        if "doc_type" in document_cols:
            where.append("COALESCE(d.doc_type,'')<>'VO'")
        sql = (
            "SELECT " + ",".join(select) +
            " FROM document_attachments a JOIN documents d ON d.id=a.document_id" +
            (" WHERE " + " AND ".join(where) if where else "") +
            " ORDER BY a.id DESC LIMIT 2000"
        )
        try:
            with connect() as conn:
                rows = conn.execute(sql, tuple(params)).fetchall()
            out = [dict(row) for row in rows]
            for item in out:
                item.setdefault("mime_type", "")
                item.setdefault("blob_size", 0)
            return out
        except Exception:
            return []

    def load_attachment(self, attachment_id: int) -> tuple[str, str, bytes]:
        cols = self._columns("document_attachments")
        if not cols:
            raise AIServiceError("Database chưa có bảng file đính kèm.")
        fields = [name for name in ("file_name", "mime_type", "file_content", "file_path") if name in cols]
        if not fields:
            raise AIServiceError("Bảng file đính kèm chưa có cột dữ liệu hỗ trợ.")
        quoted = ",".join(f'"{name}"' for name in fields)
        with connect() as conn:
            row = conn.execute(
                f"SELECT {quoted} FROM document_attachments WHERE id=%s", (int(attachment_id),)
            ).fetchone()
        if not row:
            raise AIServiceError("Không tìm thấy file đính kèm.")
        item = dict(row)
        name = str(item.get("file_name") or Path(str(item.get("file_path") or "attachment")).name)
        mime = str(item.get("mime_type") or "application/octet-stream")
        blob = item.get("file_content")
        if blob is not None:
            return name, mime, bytes(blob)
        path_text = str(item.get("file_path") or "").strip()
        if not path_text:
            raise AIServiceError("File đính kèm không còn nội dung lưu trữ.")
        path = Path(path_text)
        if not path.exists() or not path.is_file():
            raise AIServiceError("Không tìm thấy file đính kèm trên storage cục bộ.")
        return name, mime, path.read_bytes()


__all__ = [
    "AIServiceError",
    "AISettings",
    "GeminiSettings",
    "OpenAIProjectAssistant",
    "GeminiProjectAssistant",
    "ProjectContextBuilder",
    "openai_error_to_service_error",
    "gemini_error_to_service_error",
]
