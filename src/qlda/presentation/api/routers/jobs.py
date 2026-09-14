from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from qlda.presentation.api.dependencies import Principal, require_roles
from qlda.presentation.api.schemas import JobEnqueueRequest
from qlda.services import FileService, JobService, ProjectAccessService

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


@router.get("")
def list_jobs(
    project_id: int = Query(gt=0),
    job_type: str = "",
    limit: int = Query(default=20, ge=1, le=200),
    principal: Principal = Depends(require_roles("update", "admin")),
):
    ProjectAccessService.require_project(principal.user, project_id)
    return {
        "ok": True,
        "jobs": [_rowdict(row) for row in JobService.list(project_id, job_type=job_type, limit=limit)],
    }


@router.get("/stats")
def queue_stats(principal: Principal = Depends(require_roles("update", "admin"))):
    return {"ok": True, "stats": JobService.queue_stats()}


@router.post("/enqueue", status_code=status.HTTP_202_ACCEPTED)
def enqueue_job(
    request: JobEnqueueRequest,
    principal: Principal = Depends(require_roles("update", "admin")),
):
    info = FileService.info(principal.token, request.file_id)
    file_data = dict(info.get("file") or {})
    project_code = str(file_data.get("project_code") or "")
    if not project_code:
        raise HTTPException(status_code=400, detail="File chưa gắn project_code.")
    file_scope = ProjectAccessService.require_project_code(principal.user, project_code)
    meta = JobService.parse_upload_purpose(request.upload_purpose)
    if not meta:
        raise HTTPException(status_code=400, detail="upload_purpose không hợp lệ.")
    target_id = int(meta.get("workspace_project_id") or meta.get("project_id") or 0)
    target_scope = ProjectAccessService.require_project(principal.user, target_id)
    if target_scope.workspace_project_id != file_scope.workspace_project_id:
        raise HTTPException(
            status_code=400,
            detail="File và Excel job không cùng workspace dự án.",
        )
    job = JobService.enqueue_from_upload_purpose(
        request.upload_purpose,
        file_id=request.file_id,
        created_by=principal.email,
    )
    return {"ok": True, "job": _rowdict(job)}


@router.get("/{job_id}")
def get_job(
    job_id: int,
    principal: Principal = Depends(require_roles("update", "admin")),
):
    job = _rowdict(JobService.get(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy job.")
    project_id = int(job.get("project_id") or 0)
    if project_id > 0:
        ProjectAccessService.require_project(principal.user, project_id)
    return {"ok": True, "job": job}


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: int,
    principal: Principal = Depends(require_roles("update", "admin")),
):
    job = _rowdict(JobService.get(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy job.")
    project_id = int(job.get("project_id") or 0)
    if project_id > 0:
        ProjectAccessService.require_project(principal.user, project_id)
    result = JobService.request_cancel(job_id)
    return {"ok": True, "result": result}
