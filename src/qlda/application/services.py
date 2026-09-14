from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from qlda.domain.errors import AuthorizationError
from qlda.domain.models import ProjectScope
from qlda.domain.ports import (
    AIPort,
    CancelFn,
    ExcelImportPort,
    FilePort,
    JobPort,
    ProgressFn,
    ProjectAccessPort,
    SearchPort,
    SessionPort,
)


class SessionUseCases:
    def __init__(self, port: SessionPort) -> None:
        self._port = port

    def current_user(self, token: str) -> dict[str, Any]:
        return self._port.current_user(token)

    def require_roles(self, token: str, roles: Iterable[str]) -> dict[str, Any]:
        user = self.current_user(token)
        allowed = {str(role or "").strip().lower() for role in roles}
        role = str(user.get("role") or "read").strip().lower()
        if allowed and role not in allowed:
            raise AuthorizationError("Tài khoản không có quyền thực hiện thao tác này.")
        return user


class ProjectAccessUseCases:
    def __init__(self, port: ProjectAccessPort) -> None:
        self._port = port

    def require_project(self, user: dict[str, Any], project_id: int) -> ProjectScope:
        return self._port.require_project(user, project_id)

    def require_project_code(self, user: dict[str, Any], project_code: str) -> ProjectScope:
        return self._port.require_project_code(user, project_code)


class FileUseCases:
    def __init__(self, port: FilePort) -> None:
        self._port = port

    def local_path(self, file_id: str):
        return self._port.local_path(file_id)

    def info(self, token: str, file_id: str):
        return self._port.info(token, file_id)

    def list_record_files(self, token: str, **kwargs: Any):
        return self._port.list_record_files(token, **kwargs)

    def record_file_counts(self, token: str, **kwargs: Any):
        return self._port.record_file_counts(token, **kwargs)

    def save_bytes(self, token: str, **kwargs: Any):
        return self._port.save_bytes(token, **kwargs)

    def make_upload_ticket(self, token: str, **kwargs: Any):
        return self._port.make_upload_ticket(token, **kwargs)

    def trash(self, token: str, file_id: str):
        return self._port.trash(token, file_id)


class JobUseCases:
    def __init__(self, port: JobPort) -> None:
        self._port = port

    def ensure_schema(self) -> None:
        self._port.ensure_schema()

    def build_upload_purpose(self, job_type: str, project_id: int, **options: Any) -> str:
        return self._port.build_upload_purpose(job_type, project_id, **options)

    def parse_upload_purpose(self, value: str):
        return self._port.parse_upload_purpose(value)

    def enqueue(self, **kwargs: Any):
        return self._port.enqueue(**kwargs)

    def enqueue_from_upload_purpose(self, upload_purpose: str, *, file_id: str, created_by: str = ""):
        return self._port.enqueue_from_upload_purpose(
            upload_purpose, file_id=file_id, created_by=created_by
        )

    def list(self, project_id: int, *, job_type: str = "", limit: int = 10):
        return self._port.list(project_id, job_type=job_type, limit=limit)

    def get(self, job_id: int):
        return self._port.get(job_id)

    def claim_next(self, worker_id: str):
        return self._port.claim_next(worker_id)

    def request_cancel(self, job_id: int):
        return self._port.request_cancel(job_id)

    def cancel_requested(self, job_id: int) -> bool:
        return bool(self._port.cancel_requested(job_id))

    def update_progress(self, job_id: int, progress: int, stage: str, current_sheet: str = ""):
        return self._port.update_progress(job_id, progress, stage, current_sheet)

    def complete(self, job_id: int, result: dict[str, Any] | None = None):
        return self._port.complete(job_id, result)

    def fail(self, job_id: int, error_message: str, *, retry: bool = False):
        return self._port.fail(job_id, error_message, retry=retry)

    def recover_stale(self, *, stale_minutes: int = 20) -> int:
        return int(self._port.recover_stale(stale_minutes=stale_minutes))

    def resource_limits(self):
        return self._port.resource_limits()

    def memory_used_percent(self) -> float:
        return float(self._port.memory_used_percent())

    def queue_stats(self):
        return self._port.queue_stats()

    def heartbeat(self, worker_id: str, *, status: str, current_job_id: int | None = None):
        return self._port.heartbeat(worker_id, status=status, current_job_id=current_job_id)


class AIUseCases:
    def __init__(self, port: AIPort) -> None:
        self._port = port

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
        return self._port.ask(
            project_id,
            question,
            provider=provider,
            history=history,
            status_date=status_date,
            use_web=use_web,
            workspace_scope=workspace_scope,
        )

    def schedule_risk(self, project_id: int, **kwargs: Any) -> str:
        return self._port.schedule_risk(project_id, **kwargs)

    def draft_report(self, project_id: int, **kwargs: Any) -> str:
        return self._port.draft_report(project_id, **kwargs)

    def legal_qa(self, project_id: int, question: str, **kwargs: Any) -> str:
        return self._port.legal_qa(project_id, question, **kwargs)

    def test_connection(self, *, provider: str = "openai") -> str:
        return self._port.test_connection(provider=provider)


class SearchUseCases:
    def __init__(self, port: SearchPort) -> None:
        self._port = port

    def search(
        self,
        project_id: int,
        query: str,
        *,
        kinds: Iterable[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self._port.search(project_id, query, kinds=kinds, limit=limit)


class ExcelImportUseCases:
    def __init__(self, port: ExcelImportPort) -> None:
        self._port = port

    def scan_workbook(
        self,
        path: str | Path,
        *,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        return self._port.scan_workbook(path, progress=progress, cancelled=cancelled)

    def process_job(
        self,
        job: dict[str, Any],
        path: str | Path,
        file_row: dict[str, Any],
        *,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        return self._port.process_job(
            job,
            path,
            file_row,
            progress=progress,
            cancelled=cancelled,
        )


@dataclass(frozen=True)
class ApplicationServices:
    sessions: SessionUseCases
    access: ProjectAccessUseCases
    files: FileUseCases
    jobs: JobUseCases
    ai: AIUseCases
    search: SearchUseCases
    excel: ExcelImportUseCases
