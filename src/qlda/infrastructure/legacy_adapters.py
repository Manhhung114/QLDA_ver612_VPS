from __future__ import annotations

"""Remaining anti-corruption adapters for QLDA V7.2.

V7.2 retires the project-access compatibility adapter. Only AI context and the
business-heavy Excel import pipeline still depend on the deprecated V6 service
layer while they are migrated incrementally.

Domain and application packages never import this module.
"""

from typing import Any

from qlda.domain.errors import AIApplicationError


class LegacyAIAdapter:
    @staticmethod
    def _service():
        from qlda.services.ai import AIService

        return AIService

    @staticmethod
    def _translate(callable_, *args: Any, **kwargs: Any):
        from qlda.services.ai import AIApplicationError as LegacyAIApplicationError

        try:
            return callable_(*args, **kwargs)
        except LegacyAIApplicationError as exc:
            raise AIApplicationError(
                str(exc),
                code=str(getattr(exc, "code", "ai_error") or "ai_error"),
                retryable=bool(getattr(exc, "retryable", False)),
                action=str(getattr(exc, "action", "") or ""),
            ) from exc

    def ask(self, project_id: int, question: str, **kwargs: Any) -> str:
        return self._translate(self._service().ask, project_id, question, **kwargs)

    def schedule_risk(self, project_id: int, **kwargs: Any) -> str:
        return self._translate(self._service().schedule_risk, project_id, **kwargs)

    def draft_report(self, project_id: int, **kwargs: Any) -> str:
        return self._translate(self._service().draft_report, project_id, **kwargs)

    def legal_qa(self, project_id: int, question: str, **kwargs: Any) -> str:
        return self._translate(self._service().legal_qa, project_id, question, **kwargs)

    def test_connection(self, *, provider: str = "openai") -> str:
        return self._translate(self._service().test_connection, provider=provider)


class LegacyExcelImportAdapter:
    @staticmethod
    def scan_workbook(path, *, progress=None, cancelled=None):
        from qlda.services.excel import ExcelImportService

        return ExcelImportService.scan_workbook(
            path,
            progress=progress,
            cancelled=cancelled,
        )

    @staticmethod
    def process_job(job, path, file_row, *, progress=None, cancelled=None):
        from qlda.infrastructure.database import make_database
        from qlda.services.excel import ExcelImportService

        return ExcelImportService(db_factory=make_database).process_job(
            job,
            path,
            file_row,
            progress=progress,
            cancelled=cancelled,
        )
