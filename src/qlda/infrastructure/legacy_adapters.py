from __future__ import annotations

"""Remaining anti-corruption adapters for QLDA V7.1.

V7.1 retires legacy adapters for sessions, files, jobs and search. Only the
business-heavy compatibility areas below remain while their V6.x behavior is
migrated incrementally: project access policy, AI context and Excel imports.

Domain and application packages never import this module.
"""

from typing import Any

from qlda.domain.errors import AIApplicationError
from qlda.domain.models import ProjectScope


class LegacyProjectAccessAdapter:
    @staticmethod
    def _scope(value: Any) -> ProjectScope:
        return ProjectScope(
            requested_project_id=int(value.requested_project_id),
            master_project_id=int(value.master_project_id),
            workspace_project_id=int(value.workspace_project_id),
            project_code=str(value.project_code or ""),
            is_master_scope=bool(value.is_master_scope),
        )

    def require_project(self, user: dict[str, Any], project_id: int) -> ProjectScope:
        from qlda.services.access import ProjectAccessService

        return self._scope(ProjectAccessService.require_project(user, project_id))

    def require_project_code(self, user: dict[str, Any], project_code: str) -> ProjectScope:
        from qlda.services.access import ProjectAccessService

        return self._scope(ProjectAccessService.require_project_code(user, project_code))


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
