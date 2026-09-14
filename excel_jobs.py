from __future__ import annotations

"""Compatibility API for the early V6.24 Excel queue prototype.

All durable queue/schema work is owned by :mod:`excel_jobs_v624`. Keeping this
module as a thin adapter prevents the old prototype from creating or mutating a
second incompatible shape of the same ``qlda_excel_jobs`` PostgreSQL table.
"""

from typing import Any

import excel_jobs_v624 as _v624

PATCH_VERSION = _v624.PATCH_VERSION
VALID_STATUSES = _v624.ACTIVE_STATES | _v624.FINAL_STATES
VALID_JOB_TYPES = {"BOQ_IMPORT", "BOQ"}
PURPOSE_PREFIX = _v624.PURPOSE_PREFIX

ensure_schema = _v624.ensure_schema
claim_next_job = _v624.claim_next_job
get_job = _v624.get_job
request_cancel = _v624.request_cancel
cancel_requested = _v624.cancel_requested
memory_used_percent = _v624.memory_used_percent
resource_limits = _v624.resource_limits
queue_stats = _v624.queue_stats


def build_upload_purpose(job_type: str, project_id: int, **options: Any) -> str:
    return _v624.build_upload_purpose(
        "BOQ" if str(job_type or "").strip().upper() == "BOQ_IMPORT" else job_type,
        int(project_id),
        workspace_project_id=int(project_id),
        **options,
    )


def parse_upload_purpose(value: str) -> dict[str, Any] | None:
    return _v624.parse_upload_purpose(value)


def enqueue_file_job(
    *,
    file_id: str,
    project_id: int,
    job_type: str,
    options: dict[str, Any] | None = None,
    created_by: str = "",
) -> dict[str, Any]:
    return _v624.enqueue_job(
        project_id=int(project_id),
        workspace_project_id=int(project_id),
        file_id=str(file_id),
        job_type="BOQ" if str(job_type or "").strip().upper() == "BOQ_IMPORT" else str(job_type),
        options=dict(options or {}),
        created_by=created_by,
    )


def enqueue_from_upload_purpose(
    upload_purpose: str,
    *,
    file_id: str,
    created_by: str = "",
) -> dict[str, Any] | None:
    return _v624.enqueue_from_upload_purpose(
        upload_purpose,
        file_id=file_id,
        created_by=created_by,
    )


def list_project_jobs(project_id: int, *, job_type: str = "", limit: int = 10) -> list[dict[str, Any]]:
    kind = str(job_type or "").strip().upper()
    if kind == "BOQ_IMPORT":
        kind = "BOQ"
    return _v624.list_jobs(int(project_id), job_type=kind, limit=limit)


def update_job(job_id: int, *, progress: int, current_step: str) -> None:
    _v624.update_progress(int(job_id), int(progress), str(current_step or ""), "")


def heartbeat_job(job_id: int) -> None:
    job = _v624.get_job(int(job_id)) or {}
    _v624.update_progress(
        int(job_id),
        int(job.get("progress") or 1),
        str(job.get("stage") or "Đang xử lý"),
        str(job.get("current_sheet") or ""),
    )


def complete_job(job_id: int, result: dict[str, Any] | None = None) -> dict[str, Any]:
    _v624.complete_job(int(job_id), result)
    return _v624.get_job(int(job_id)) or {}


def fail_job(job_id: int, error: Any) -> dict[str, Any]:
    _v624.fail_job(int(job_id), str(error or "Lỗi xử lý Excel"), retry=False)
    return _v624.get_job(int(job_id)) or {}


def requeue_stale_jobs(*, stale_minutes: int = 20, max_attempts: int = 3) -> int:
    # max_attempts is retained only for call compatibility; V6.24.2 stores each
    # job's own max_attempts value and uses that as the authoritative limit.
    _ = max_attempts
    return _v624.recover_stale_jobs(stale_minutes=int(stale_minutes))
