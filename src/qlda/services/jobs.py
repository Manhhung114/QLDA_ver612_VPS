from __future__ import annotations

from typing import Any

from qlda.modules.excel import jobs


class JobService:
    """Application-facing API for the durable Excel job queue."""

    @staticmethod
    def ensure_schema() -> None:
        jobs.ensure_schema()

    @staticmethod
    def build_upload_purpose(job_type: str, project_id: int, **options: Any) -> str:
        return jobs.build_upload_purpose(job_type, project_id, **options)

    @staticmethod
    def parse_upload_purpose(value: str):
        return jobs.parse_upload_purpose(value)

    @staticmethod
    def enqueue(**kwargs: Any):
        return jobs.enqueue_job(**kwargs)

    @staticmethod
    def enqueue_from_upload_purpose(upload_purpose: str, *, file_id: str, created_by: str = ""):
        return jobs.enqueue_from_upload_purpose(
            upload_purpose,
            file_id=file_id,
            created_by=created_by,
        )

    @staticmethod
    def list(project_id: int, *, job_type: str = "", limit: int = 10):
        return jobs.list_jobs(project_id, job_type=job_type, limit=limit)

    @staticmethod
    def get(job_id: int):
        return jobs.get_job(job_id)

    @staticmethod
    def claim_next(worker_id: str):
        return jobs.claim_next_job(worker_id)

    @staticmethod
    def request_cancel(job_id: int):
        return jobs.request_cancel(job_id)

    @staticmethod
    def cancel_requested(job_id: int) -> bool:
        return bool(jobs.cancel_requested(job_id))

    @staticmethod
    def update_progress(job_id: int, progress: int, stage: str, current_sheet: str = ""):
        return jobs.update_progress(job_id, progress, stage, current_sheet)

    @staticmethod
    def complete(job_id: int, result: dict[str, Any] | None = None):
        return jobs.complete_job(job_id, result)

    @staticmethod
    def fail(job_id: int, error_message: str, *, retry: bool = False):
        return jobs.fail_job(job_id, error_message, retry=retry)

    @staticmethod
    def recover_stale(*, stale_minutes: int = 20) -> int:
        return int(jobs.recover_stale_jobs(stale_minutes=stale_minutes))

    @staticmethod
    def resource_limits():
        return jobs.resource_limits()

    @staticmethod
    def memory_used_percent() -> float:
        return float(jobs.memory_used_percent())

    @staticmethod
    def queue_stats():
        return jobs.queue_stats()

    @staticmethod
    def heartbeat(worker_id: str, *, status: str, current_job_id: int | None = None):
        return jobs.worker_heartbeat(
            worker_id,
            status=status,
            current_job_id=current_job_id,
        )
