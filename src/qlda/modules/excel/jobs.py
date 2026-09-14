from __future__ import annotations

from typing import Any

from qlda.runtime import legacy_import


def _api():
    # V6.25 is intentionally strangler-style: callers move to this stable
    # namespace first; V6.26 can replace internals without changing callers.
    return legacy_import("excel_jobs_v624")


def build_upload_purpose(job_type: str, project_id: int, **options: Any) -> str:
    return _api().build_upload_purpose(job_type, project_id, **options)


def parse_upload_purpose(value: str):
    return _api().parse_upload_purpose(value)


def enqueue_job(**kwargs: Any):
    return _api().enqueue_job(**kwargs)


def enqueue_from_upload_purpose(upload_purpose: str, *, file_id: str, created_by: str = ""):
    return _api().enqueue_from_upload_purpose(
        upload_purpose,
        file_id=file_id,
        created_by=created_by,
    )


def list_jobs(project_id: int, *, job_type: str = "", limit: int = 10):
    return _api().list_jobs(project_id, job_type=job_type, limit=limit)


def claim_next_job(worker_id: str):
    return _api().claim_next_job(worker_id)


def get_job(job_id: int):
    return _api().get_job(job_id)


def request_cancel(job_id: int):
    return _api().request_cancel(job_id)


def cancel_requested(job_id: int) -> bool:
    return bool(_api().cancel_requested(job_id))


def update_progress(job_id: int, progress: int, stage: str, current_sheet: str = ""):
    return _api().update_progress(job_id, progress, stage, current_sheet)


def complete_job(job_id: int, result: dict[str, Any] | None = None):
    return _api().complete_job(job_id, result)


def fail_job(job_id: int, error_message: str, *, retry: bool = False):
    return _api().fail_job(job_id, error_message, retry=retry)


def recover_stale_jobs(*, stale_minutes: int = 20) -> int:
    return int(_api().recover_stale_jobs(stale_minutes=stale_minutes))


def memory_used_percent() -> float:
    return float(_api().memory_used_percent())


def resource_limits():
    return _api().resource_limits()


def queue_stats():
    return _api().queue_stats()


def __getattr__(name: str):
    # Constants and less common compatibility functions remain available while
    # the implementation is physically moved in later V6.25 substeps.
    return getattr(_api(), name)
