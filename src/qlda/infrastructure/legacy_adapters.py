from __future__ import annotations

"""Anti-corruption adapters from the V7 clean core to proven V6.x runtime code.

All versioned/legacy compatibility is kept on the infrastructure side of the
dependency rule. Domain and application packages never import this module.
"""

from pathlib import Path
from typing import Any, Iterable

from qlda.domain.errors import AIApplicationError, AuthenticationError
from qlda.domain.models import ProjectScope


class LegacySessionAdapter:
    def current_user(self, token: str) -> dict[str, Any]:
        from qlda.services.auth import AuthenticationError as LegacyAuthenticationError
        from qlda.services.auth import SessionService

        try:
            return SessionService.current_user(token)
        except LegacyAuthenticationError as exc:
            raise AuthenticationError(str(exc)) from exc


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


class LegacyFileAdapter:
    def local_path(self, file_id: str) -> tuple[dict[str, Any], Path]:
        from qlda.services.files import FileService

        return FileService.local_path(file_id)

    def info(self, token: str, file_id: str):
        from qlda.services.files import FileService

        return FileService.info(token, file_id)

    def list_record_files(self, token: str, **kwargs: Any):
        from qlda.services.files import FileService

        return FileService.list_record_files(token, **kwargs)

    def record_file_counts(self, token: str, **kwargs: Any):
        from qlda.services.files import FileService

        return FileService.record_file_counts(token, **kwargs)

    def save_bytes(self, token: str, **kwargs: Any):
        from qlda.services.files import FileService

        return FileService.save_bytes(token, **kwargs)

    def make_upload_ticket(self, token: str, **kwargs: Any):
        from qlda.services.files import FileService

        return FileService.make_upload_ticket(token, **kwargs)

    def trash(self, token: str, file_id: str):
        from qlda.services.files import FileService

        return FileService.trash(token, file_id)


class LegacyJobAdapter:
    @staticmethod
    def _service():
        from qlda.services.jobs import JobService

        return JobService

    def ensure_schema(self) -> None:
        self._service().ensure_schema()

    def build_upload_purpose(self, job_type: str, project_id: int, **options: Any) -> str:
        return self._service().build_upload_purpose(job_type, project_id, **options)

    def parse_upload_purpose(self, value: str):
        return self._service().parse_upload_purpose(value)

    def enqueue(self, **kwargs: Any):
        return self._service().enqueue(**kwargs)

    def enqueue_from_upload_purpose(self, upload_purpose: str, *, file_id: str, created_by: str = ""):
        return self._service().enqueue_from_upload_purpose(
            upload_purpose,
            file_id=file_id,
            created_by=created_by,
        )

    def list(self, project_id: int, *, job_type: str = "", limit: int = 10):
        return self._service().list(project_id, job_type=job_type, limit=limit)

    def get(self, job_id: int):
        return self._service().get(job_id)

    def claim_next(self, worker_id: str):
        return self._service().claim_next(worker_id)

    def request_cancel(self, job_id: int):
        return self._service().request_cancel(job_id)

    def cancel_requested(self, job_id: int) -> bool:
        return bool(self._service().cancel_requested(job_id))

    def update_progress(self, job_id: int, progress: int, stage: str, current_sheet: str = ""):
        return self._service().update_progress(job_id, progress, stage, current_sheet)

    def complete(self, job_id: int, result: dict[str, Any] | None = None):
        return self._service().complete(job_id, result)

    def fail(self, job_id: int, error_message: str, *, retry: bool = False):
        return self._service().fail(job_id, error_message, retry=retry)

    def recover_stale(self, *, stale_minutes: int = 20) -> int:
        return int(self._service().recover_stale(stale_minutes=stale_minutes))

    def resource_limits(self):
        return self._service().resource_limits()

    def memory_used_percent(self) -> float:
        return float(self._service().memory_used_percent())

    def queue_stats(self):
        return self._service().queue_stats()

    def heartbeat(self, worker_id: str, *, status: str, current_job_id: int | None = None):
        return self._service().heartbeat(
            worker_id,
            status=status,
            current_job_id=current_job_id,
        )


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


class LegacySearchAdapter:
    def search(
        self,
        project_id: int,
        query: str,
        *,
        kinds: Iterable[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        from qlda.services.search import SearchService

        return SearchService.search(project_id, query, kinds=kinds, limit=limit)


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
